"""Native four-limb solver outputs measured relative to their own zero-input poses."""

import hashlib
import json
import uuid

import bpy
import numpy as np
from mathutils import Matrix

from . import limb_ik_fk, root_control, torso_controls

KEY = 'character_designer_limb_bind_graph_v1'
OWNER = 'character_designer_limb_bind_owner_v1'
COPY = 'CD Bind Relative Limb'
FOLLOW = 'CD Bind Solver Space'


def _require(value, message):
    if not value:
        raise ValueError(message)


def _update(context):
    context.view_layer.update()


def _copy(owner, target, subtarget, name, target_space, owner_space):
    constraint = owner.constraints.new('COPY_TRANSFORMS')
    constraint.name = name
    constraint.target = target
    constraint.subtarget = subtarget
    constraint.target_space = target_space
    constraint.owner_space = owner_space
    constraint.remove_target_shear = False
    return constraint


def _limbs(rig, inventory):
    entries = inventory['rigs']
    _require(
        set(entries) == {(k, s) for k in ('ARM', 'LEG') for s in ('L', 'R')},
        'Bind-relative posing needs the four authenticated limb chains.',
    )
    native = []
    controls = set()
    drivers = set()
    relations = set()
    ik_rows = []
    for key, entry in entries.items():
        native.extend(limb_ik_fk.pose_names(entry))
        controls.update((entry['target'].name, entry['pole'].name))
        root = rig.pose.bones[entry['chain'][0]]
        if root.parent:
            controls.add(root.parent.name)
        foot = entry.get('foot_controls')
        if key[0] == 'LEG':
            _require(foot is not None, 'Both legs need their current Toe Bend and Foot Roll setup.')
            controls.update((foot['roll'], foot['toe_control']))
            drivers.update((row['path'], row['index']) for row in foot['drivers'])
            relations.update((row['owner'], row['name']) for row in foot['constraints'])
        for pb, con, record in entry['entries']:
            relations.add((pb.name, con.name))
            if limb_ik_fk.is_switch_constraint(rig, pb, con, record):
                drivers.add((con.path_from_id('influence'), 0))
            if con.type == 'IK':
                _require(record['role'] == 'IK' and not con.mute, 'The original limb IK must be enabled.')
                ik_rows.append((pb.name, con.name, bool(con.mute)))
    _require(
        len(set(native)) == len(native) == 14 and len(ik_rows) == 4 and not controls.intersection(native),
        'The native limb and control ownership overlaps.',
    )
    return native, sorted(controls), drivers, relations, ik_rows


def _clear_properties(owner):
    for key in tuple(owner.keys()):
        del owner[key]


def _source_object(rig, collection, token, state):
    src = rig.copy()
    state['source'] = src
    data = rig.data.copy()
    state['data'] = data
    src.data = data
    src.parent = None
    src.matrix_world = Matrix.Identity(4)
    src.name = 'CD Limb Solver'
    data.name = 'CD Limb Solver'
    _clear_properties(src)
    _clear_properties(data)
    data.animation_data_clear()
    for bone in data.bones:
        _clear_properties(bone)
    src[OWNER] = token
    data[OWNER] = token
    ad = src.animation_data
    if ad:
        ad.action = None
        for track in list(ad.nla_tracks):
            ad.nla_tracks.remove(track)
    for c in list(src.constraints):
        src.constraints.remove(c)
    for mod in list(src.modifiers):
        src.modifiers.remove(mod)
    src.hide_render = True
    src.hide_select = True
    collection.objects.link(src)
    src.hide_set(True)
    return src


def _raw_driver(src, rig, name, field, axis):
    pb = src.pose.bones[name]
    f = pb.driver_add(field, axis)
    # Blender seeds a 0..1 identity curve. Its key search snaps small positive
    # angles to zero; a keyless driver passes through the native float exactly.
    f.keyframe_points.clear()
    f.driver.type = 'AVERAGE'
    v = f.driver.variables.new()
    v.type = 'SINGLE_PROP'
    ref = v.targets[0]
    ref.id = rig
    ref.data_path = rig.pose.bones[name].path_from_id(field) + f'[{axis}]'


def _sync_rotation_modes(src, rig, inventory):
    """Auto Align chooses between the two authenticated end-rotation constraints."""
    for entry in inventory['rigs'].values():
        for pb, con, record in entry['entries']:
            if record['role'] not in {'END_ROTATION', 'AUTO_OFFSET_ROTATION'}:
                continue
            target = src.pose.bones[pb.name].constraints[con.name]
            f = target.driver_add('mute')
            f.keyframe_points.clear()
            f.driver.type = 'AVERAGE'
            v = f.driver.variables.new()
            v.type = 'SINGLE_PROP'
            ref = v.targets[0]
            ref.id = rig
            ref.data_path = con.path_from_id('mute')


def _prepare_source(src, rig, drivers, relations):
    for p in src.pose.bones:
        p.matrix_basis = Matrix.Identity(4)
        p.custom_shape = None
        p.custom_shape_transform = None
        _clear_properties(p)
        for con in list(p.constraints):
            if (p.name, con.name) not in relations:
                p.constraints.remove(con)
                continue
            for field in ('target', 'pole_target', 'space_object'):
                if getattr(con, field, None) == rig:
                    setattr(con, field, src)
    for f in list(src.animation_data.drivers):
        if (f.data_path, f.array_index) not in drivers:
            src.animation_data.drivers.remove(f)
            continue
        for v in f.driver.variables:
            for ref in v.targets:
                if ref.id == rig:
                    ref.id = src


def _input_scope(rig, native, controls, drivers, relations):
    """Include only the native parents and internal bone targets needed by these limbs."""
    certified = {}
    root = root_control.get_record(rig)
    torso = torso_controls.get_record(rig)
    for record in (root, torso):
        if record:
            certified.update(((row['owner'], row['name']), row) for row in record['constraints'])
    names = set()
    todo = list(native) + list(controls)
    while todo:
        name = todo.pop()
        if name in names:
            continue
        names.add(name)
        p = rig.pose.bones[name]
        if p.parent:
            todo.append(p.parent.name)
        for con in p.constraints:
            _require(
                (name, con.name) in relations or (name, con.name) in certified,
                'An unauthenticated constraint affects an upstream limb input: ' + name,
            )
            row = certified.get((name, con.name))
            if row:
                _require(
                    con.type == row['type']
                    and con.target == rig
                    and not con.mute
                    and all(
                        torso_controls._same_value(getattr(con, key), value)
                        for key, value in row['fields'].items()
                    ),
                    'The registered upstream constraint changed: ' + name,
                )
            relations.add((name, con.name))
            for field, sub in [
                ('target', 'subtarget'),
                ('pole_target', 'pole_subtarget'),
                ('space_object', 'space_subtarget'),
            ]:
                ref = getattr(con, field, None)
                if ref:
                    target = getattr(con, sub, '')
                    _require(
                        ref == rig and target in rig.pose.bones,
                        'A limb dependency is not a local bone: ' + name,
                    )
                    todo.append(target)
    conditional_paths = tuple(rig.pose.bones[n].constraints[c].path_from_id() + '.' for n, c in relations)
    for f in rig.animation_data.drivers:
        if f.data_path.startswith(conditional_paths):
            _require(
                (f.data_path, f.array_index) in drivers,
                'Another driver affects a bind-relative limb dependency.',
            )
    return names


def _near_unit_scale(values):
    # Registered native FK channels can be decomposed repeatedly when
    # matching IK/FK. This allowance is used only for a retained source.
    return all(abs(x - 1.0) <= 64 * 2.0 ** -23 for x in values)


def _require_neutral(rig, inventory, held_scales=None):
    """Calibrate only after an explicit whole-character clear on the current frame."""
    for e in inventory['rigs'].values():
        names = set(limb_ik_fk.pose_names(e))
        names.update((e['target'].name, e['pole'].name))
        foot = e.get('foot_controls')
        if foot:
            names.update((foot['roll'], foot['toe_control']))
        for n in names:
            p = rig.pose.bones[n]
            pos = all(locked or abs(x) < 1e-7 for x, locked in zip(p.location, p.lock_location))
            size = all(locked or abs(x - 1.0) < 1e-7 for x, locked in zip(p.scale, p.lock_scale))
            if held_scales and n in held_scales:
                size = tuple(p.scale) == held_scales[n] and _near_unit_scale(p.scale)
            rot = p.matrix_basis.to_quaternion()
            # A locked channel is not an authored input of the whole-body clear.
            turn = all(locked or abs(x) < 1e-7 for x, locked in zip((rot.x, rot.y, rot.z), p.lock_rotation))
            _require(pos and size and turn, 'Clear the editable limb transforms before calibrating: ' + n)
        upstream = rig.pose.bones[e['chain'][0]].parent
        if upstream:
            delta = np.asarray(upstream.matrix) - np.asarray(upstream.bone.matrix_local)
            _require(
                np.max(np.abs(delta)) < 5.0e-5,
                'Restore the upstream body reference before calibrating: ' + upstream.name,
            )


def _inputs(src, rig, names, existing):
    # Each solver has its own raw roll input. Its neutral copy freezes this
    # channel. Reading the artist's live roll on both copies cancels heel lift.
    from . import foot_controls
    hinges = [foot for foot in foot_controls.records(rig).values() if foot.get('roll_hinge') == 1]
    hinge_inputs = {
        rig.pose.bones[foot['roll']].path_from_id('rotation_euler') + '[0]'
        for foot in hinges
    }
    # The unrolled reference mirrors the inputs of this *copy's* leg. Its
    # IK can differ from the public character's IK in the Stable branch.
    reference_paths = tuple(rig.pose.bones[name].path_from_id() + '.'
                            for foot in hinges for role, name in foot['bones'].items()
                            if role.startswith('UNROLLED_'))
    for f in src.animation_data.drivers:
        for v in f.driver.variables:
            for ref in v.targets:
                if ref.id == src and v.type == 'SINGLE_PROP':
                    if (ref.data_path not in hinge_inputs and not f.data_path.startswith(reference_paths)
                            and not ref.data_path.startswith(reference_paths)):
                        ref.id = rig
    for name in sorted(names):
        orig = rig.pose.bones[name]
        rot = {'QUATERNION': 'rotation_quaternion', 'AXIS_ANGLE': 'rotation_axis_angle'}.get(
            orig.rotation_mode, 'rotation_euler'
        )
        for field in ('location', rot, 'scale'):
            for axis in range(len(getattr(orig, field))):
                if (orig.path_from_id(field), axis) not in existing:
                    _raw_driver(src, rig, name, field, axis)


def _solver_signature(src, rig):
    """Authenticate a helper's native graph before a later operation can use or remove it."""
    payload = []
    for b in src.data.bones:
        payload.append(
            (
                b.name,
                b.parent.name if b.parent else '',
                tuple(b.head_local),
                tuple(b.tail_local),
                np.asarray(b.matrix_local).tolist(),
                b.use_connect,
                b.inherit_scale,
            )
        )
    drivers = {(curve.data_path, curve.array_index) for curve in src.animation_data.drivers}
    for p in src.pose.bones:
        for con in p.constraints:
            fields = []
            for prop in con.bl_rna.properties:
                key = prop.identifier
                if prop.is_readonly or key in {'show_expanded', 'active', 'is_override_data', 'enabled'}:
                    continue
                if (con.path_from_id(key), 0) in drivers:
                    continue
                val = getattr(con, key)
                if prop.type == 'POINTER':
                    val = (
                        'solver'
                        if val == src
                        else 'rig'
                        if val == rig
                        else None
                        if val is None
                        else val.name_full
                    )
                elif getattr(prop, 'is_array', False):
                    val = np.asarray(val).tolist()
                fields.append((key, val))
            payload.append((p.name, con.type, fields))
    for f in src.animation_data.drivers:
        d = f.driver
        variables = []
        for v in d.variables:
            refs = []
            for ref in v.targets:
                target = (
                    'solver'
                    if ref.id == src
                    else 'rig'
                    if ref.id == rig
                    else None
                    if ref.id is None
                    else ref.id.name_full
                )
                refs.append(
                    (
                        target,
                        ref.data_path,
                        ref.bone_target,
                        ref.transform_type,
                        ref.transform_space,
                        ref.rotation_mode,
                    )
                )
            variables.append((v.name, v.type, refs))
        points = [
            (tuple(k.co), k.interpolation, tuple(k.handle_left), tuple(k.handle_right))
            for k in f.keyframe_points
        ]
        payload.append(
            (f.data_path, f.array_index, f.mute, len(f.modifiers), d.type, d.expression, variables, points)
        )
    return hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()


def _offset_hash(values):
    return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()


def _offset_signature(frames):
    return _offset_hash(
        [
            (name, np.asarray(frame.constraints[FOLLOW].inverse_matrix).tolist())
            for name, frame in sorted(frames.items())
        ]
    )


def _rig_references(rig, record):
    # The native user map lists an owning ID once regardless of how many
    # constraints or custom properties inside that ID refer to a helper.
    ids = [record['collection'], record['source'], record['source'].data]
    if record.get('neutral_source'):
        ids.extend((record['neutral_source'], record['neutral_source'].data))
    ids.extend(frame for name, frame in sorted(record['frames'].items()))
    if record.get('stable'):
        from . import natural_stable_solver
        ids.extend(natural_stable_solver.references(record['stable']))
    return list(map(rig.user_of_id, ids))


def refresh_references(rig, record):
    record['rig_references'] = _rig_references(rig, record)
    if KEY in rig:
        rig[KEY]['rig_references'] = _rig_references(rig, rig[KEY])


def active_branch(rec):
    branch = rec.get('stable')
    return branch if branch and branch['mix'] == 1. else rec


def solver_kind(rig):
    rec = rig.get(KEY)
    branch = rec.get('stable') if rec else None
    if not branch or branch['mix'] == 0.:
        return 'DIRECT'
    return branch['kind'] if branch['mix'] == 1. else 'MIXED'


def restore_prior_solver(rec, kind):
    """Restore the solver route of a saved preview that predates offset snapshots."""
    from . import natural_stable_solver
    stable = rec.get('stable')
    _require(kind == 'DIRECT' or stable and kind == stable['kind'], 'The prior limb solver is unavailable.')
    if stable:
        natural_stable_solver.set_mix(rec, float(kind == stable['kind']))


def freeze_preview(rig, rec):
    from . import natural_bind_reference
    _require(_rig_references(rig, rec) == list(rec['rig_references']),
             'Another reference on this rig uses a bind helper.')
    branches = [branch for branch in (rec, rec.get('stable')) if branch and branch.get('neutral_source')]
    ready = [(branch, natural_bind_reference.prepare_freeze(rig, branch)) for branch in branches]
    for branch, offsets in ready:
        natural_bind_reference.freeze(rig, branch, offsets)


def _relative(context, rig, src, collection, native, token, state):
    _update(context)
    for n in native:
        frame = bpy.data.objects.new('CD Bind ' + n, None)
        state['frames'][n] = frame
        collection.objects.link(frame)
        frame[OWNER] = token
        frame.hide_select = True
        frame.hide_render = True
        frame.hide_set(True)
        frame.empty_display_size = 0.01
        follow = frame.constraints.new('CHILD_OF')
        follow.name = FOLLOW
        follow.target = src
        follow.subtarget = n
        follow.target_space = 'POSE'
        follow.set_inverse_pending = False
        source = np.array(src.pose.bones[n].matrix, dtype=np.float64)
        rest = np.array(rig.data.bones[n].matrix_local, dtype=np.float64)
        follow.inverse_matrix = Matrix(np.linalg.solve(source, rest).tolist())


def _dispose(rig, state):
    for pb, con in reversed(state['copies']):
        pb.constraints.remove(con)
    for bone, name, original in state['ik_rows']:
        rig.pose.bones[bone].constraints[name].mute = original
    for frame in state['frames'].values():
        bpy.data.objects.remove(frame, do_unlink=True)
    if state.get('source'):
        bpy.data.objects.remove(state['source'], do_unlink=True)
    if state.get('data'):
        bpy.data.armatures.remove(state['data'])
    if state.get('collection'):
        bpy.data.collections.remove(state['collection'])


def install(context, rig, inventory, *, _fault=None, held_scales=None, solver='STABLE_MCH_ORI'):
    """Build a separate native evaluation graph; original actions are never copied or keyed."""
    _require(KEY not in rig, 'A bind-relative graph already exists on this rig.')
    native, controls, drivers, relations, iks = _limbs(rig, inventory)
    inputs = _input_scope(rig, native, controls, drivers, relations)
    _require_neutral(rig, inventory, held_scales)
    _require(
        all(not rig.pose.bones[n].constraints.get(COPY) for n in native),
        'A limb output name is already in use.',
    )
    token = uuid.uuid4().hex
    state = dict(frames={}, copies=[], ik_rows=iks)
    undo_reference = None
    undo_stable = None
    from . import natural_stable_solver
    _require(solver in {'DIRECT', natural_stable_solver.KIND}, 'Unknown limb solver.')
    try:
        collection = bpy.data.collections.new('CD Limb Bind')
        state['collection'] = collection
        collection[OWNER] = token
        context.scene.collection.children.link(collection)
        source = _source_object(rig, collection, token, state)
        _prepare_source(source, rig, drivers, relations)
        _inputs(source, rig, inputs, drivers)
        _sync_rotation_modes(source, rig, inventory)
        _relative(context, rig, source, collection, native, token, state)
        if _fault:
            _fault('source')
        for n in native:
            p = rig.pose.bones[n]
            con = _copy(p, state['frames'][n], '', COPY, 'WORLD', 'POSE')
            state['copies'].append((p, con))
            con.mute = True
        if _fault:
            _fault('outputs')
        rig[KEY] = {
            'version': 1,
            'token': token,
            'source': source,
            'collection': collection,
            'solver_signature': _solver_signature(source, rig),
            'frames': state['frames'],
            'offset_signature': _offset_signature(state['frames']),
            'native': json.dumps(native),
            'ik': json.dumps(iks),
            'attached': True,
            'enabled': False,
        }
        rig[KEY]['rig_references'] = _rig_references(rig, rig[KEY])
        set_enabled(context, rig, True)
        from . import natural_bind_reference
        if solver == natural_stable_solver.KIND:
            undo_stable = natural_stable_solver.attach(context, rig, rig[KEY], inventory)
            natural_stable_solver.align_knees(context, rig, rig[KEY]['stable'], inventory)
        undo_reference = natural_bind_reference.enable(context, rig, active_branch(rig[KEY]))
        if _fault:
            _fault('activate')
        return validate(rig)
    except BaseException:
        if undo_reference:
            undo_reference()
        if undo_stable:
            undo_stable()
        if KEY in rig:
            del rig[KEY]
        _dispose(rig, state)
        _update(context)
        raise


def validate(rig):
    rec = rig.get(KEY)
    _require(rec and rec.get('version') == 1, 'The bind graph record is missing or unsupported.')
    token = rec['token']
    src, collection = rec['source'], rec['collection']
    _require(
        src
        and src != rig
        and src.type == 'ARMATURE'
        and src.data != rig.data
        and all(a.get(OWNER) == token for a in (src, src.data, collection)),
        'The private solver ownership changed.',
    )
    _require(
        src.parent is None
        and src.matrix_basis == Matrix.Identity(4)
        and not src.modifiers
        and not src.constraints
        and not src.animation_data.action
        and not src.animation_data.nla_tracks
        and not src.data.animation_data
        and not collection.children,
        'The private solver was reparented, animated or edited.',
    )
    frames = dict(rec['frames'].items())
    native = json.loads(rec['native'])
    active = bool(rec['enabled'])
    attached = bool(rec['attached'])
    ik_rows = json.loads(rec['ik'])
    reference = rec.get('neutral_source')
    objects = {src, *frames.values()}
    if reference:
        from . import natural_bind_reference
        objects.add(reference)
    stable = rec.get('stable')
    if stable:
        from . import natural_stable_solver
        objects.update(natural_stable_solver.objects(stable))
        natural_stable_solver.validate(rig, rec)
    _require(
        len(native) == len(frames) == 14
        and set(frames) == set(native)
        and len(ik_rows) == 4
        and (attached or not active)
        and set(collection.objects) == objects,
        'The recorded solver objects changed.',
    )
    from . import body_original_mode as original
    session = json.loads(rig[original.SESSION]) if original.SESSION in rig else {}
    original_token = session.get('bind_pose', {}).get('token')
    original_mutes = {
        e['bone'] for e in session.get('constraints', ())
        if e['name'] == COPY and e['type'] == 'COPY_TRANSFORMS' and e['mute'] is False
    } if original_token == token and active else set()
    for n in native:
        obj = frames[n]
        _require(
            obj
            and obj.get(OWNER) == token
            and obj.type == 'EMPTY'
            and len(obj.constraints) == (4 if reference else 1) + bool(stable)
            and obj.parent is None
            and (reference or not obj.animation_data)
            and obj.matrix_basis == Matrix.Identity(4),
            'A bind output frame changed.',
        )
        follow = obj.constraints.get(FOLLOW)
        if reference:
            natural_bind_reference.validate_frame(rig, rec, obj, n)
        p = rig.pose.bones.get(n)
        con = p.constraints.get(COPY) if p else None
        _require(
            follow
            and follow.type == 'CHILD_OF'
            and follow.target == src
            and follow.subtarget == n
            and follow.target_space == 'POSE'
            and follow.owner_space == 'WORLD'
            and all(
                getattr(follow, 'use_' + kind + '_' + axis)
                for kind in ('location', 'rotation', 'scale')
                for axis in 'xyz'
            )
            and follow.mute == bool(reference)
            and follow.influence == 1
            and p
            and (
                not attached
                and con is None
                or attached
                and con
                and con.type == 'COPY_TRANSFORMS'
                and con.target == obj
                and con.subtarget == ''
                and con.target_space == 'WORLD'
                and con.owner_space == 'POSE'
                and con.influence == 1
                and con.mix_mode == 'REPLACE'
                and not con.remove_target_shear
                and (con.mute != active or n in original_mutes)
                and p.constraints[-1] == con
            ),
            'A bind output relation changed: ' + n,
        )
    for bone, name, original in ik_rows:
        c = rig.pose.bones[bone].constraints.get(name)
        twin = src.pose.bones[bone].constraints.get(name)
        _require(
            original is False
            and c
            and c.type == 'IK'
            and c.target == rig
            and c.mute == active
            and twin
            and twin.type == 'IK'
            and twin.target == src
            and twin.subtarget == c.subtarget,
            'An original limb IK relation changed: ' + bone,
        )
    _require(
        _offset_signature(frames) == rec['offset_signature'], 'The registered limb bind offsets changed.'
    )
    return rec


def set_enabled(context, rig, enabled, *, evaluate=True):
    rec = validate(rig)
    if not rec['attached']:
        if enabled:
            set_attached(context, rig, True, evaluate=evaluate)
        return
    old = bool(rec['enabled'])
    if old == enabled:
        return
    rows = json.loads(rec['ik'])
    names = json.loads(rec['native'])

    def apply(flag):
        for bone, name, previous in rows:
            rig.pose.bones[bone].constraints[name].mute = True if flag else previous
        for n in names:
            rig.pose.bones[n].constraints[COPY].mute = not flag
        rec['enabled'] = flag
        if evaluate:
            _update(context)

    try:
        apply(bool(enabled))
        validate(rig)
    except BaseException:
        apply(old)
        raise


def set_attached(context, rig, flag, *, evaluate=False):
    """Remove the bind outputs entirely for old motion, retaining the calibrated source for a keyed frame."""
    rec = validate(rig)
    if bool(rec['attached']) == bool(flag):
        if flag and not rec['enabled']:
            set_enabled(context, rig, True, evaluate=evaluate)
        return
    collection = rec['collection']
    parent = context.scene.collection
    linked = parent.children.get(collection.name) == collection
    _require(linked == bool(rec['attached']), 'The bind collection was moved to another scene.')
    _require(
        _rig_references(rig, rec) == list(rec['rig_references']),
        'Another reference on this rig uses a bind helper.',
    )
    native = json.loads(rec['native'])
    frames = dict(rec['frames'].items())
    iks = json.loads(rec['ik'])
    before = bool(rec['enabled'])

    def configure(attached, enabled):
        if attached and parent.children.get(collection.name) != collection:
            parent.children.link(collection)
            for obj in collection.objects:
                obj.hide_set(True)
        for name in native:
            pb = rig.pose.bones[name]
            c = pb.constraints.get(COPY)
            if attached:
                if c is None:
                    c = _copy(pb, frames[name], '', COPY, 'WORLD', 'POSE')
                c.mute = not enabled
            elif c is not None:
                pb.constraints.remove(c)
        for bone, name, original in iks:
            rig.pose.bones[bone].constraints[name].mute = enabled or original
        if not attached and parent.children.get(collection.name) == collection:
            parent.children.unlink(collection)
        rec['attached'] = attached
        rec['enabled'] = enabled
        rec['rig_references'] = _rig_references(rig, rec)
        if evaluate:
            _update(context)

    try:
        configure(bool(flag), bool(flag))
        validate(rig)
    except BaseException:
        configure(not bool(flag), before)
        raise


def recalibrate(context, rig, inventory, *, held_scales=None, solver='STABLE_MCH_ORI'):
    """Refit the native zero for the current IK/FK blend. Return the outer transaction's rollback."""
    rec = validate(rig)
    _require(_rig_references(rig, rec) == list(rec['rig_references']),
             'Another reference on this rig uses a bind helper.')
    _require_neutral(rig, inventory, held_scales)
    src = rec['source']
    _require(_solver_signature(src, rig) == rec['solver_signature'], 'The bind solver inputs changed.')
    frames = {n: frame.constraints[FOLLOW] for n, frame in rec['frames'].items()}
    old = {n: c.inverse_matrix.copy() for n, c in frames.items()}
    enabled = bool(rec['enabled'])
    attached = bool(rec['attached'])
    undo_reference = None
    undo_stable = None
    from . import natural_stable_solver
    _require(solver in {'DIRECT', natural_stable_solver.KIND}, 'Unknown limb solver.')
    old_mix = rec['stable']['mix'] if rec.get('stable') else None
    branch = rec
    branch_old = None
    undo_knees = None

    def rollback():
        if undo_reference:
            undo_reference()
        if branch_old is not None:
            for n, mat in branch_old.items():
                branch['frames'][n].constraints[FOLLOW].inverse_matrix = mat
            branch['offset_signature'] = _offset_signature(branch['frames'])
        if undo_knees:
            undo_knees()
        if undo_stable:
            undo_stable()
        elif old_mix is not None:
            natural_stable_solver.set_mix(rec, old_mix)
        for n, c in frames.items():
            c.inverse_matrix = old[n]
        rec['offset_signature'] = _offset_signature(rec['frames'])
        if attached:
            set_enabled(context, rig, enabled, evaluate=False)
        else:
            set_attached(context, rig, False, evaluate=False)
        _update(context)

    try:
        if not attached:
            set_attached(context, rig, True, evaluate=False)
        if solver == natural_stable_solver.KIND:
            undo_stable = natural_stable_solver.attach(context, rig, rec, inventory)
            natural_stable_solver.set_mix(rec, 1.)
            branch = rec['stable']
            undo_knees = natural_stable_solver.align_knees(context, rig, branch, inventory)
            branch_old = {n: obj.constraints[FOLLOW].inverse_matrix.copy() for n, obj in branch['frames'].items()}
        elif rec.get('stable'):
            natural_stable_solver.set_mix(rec, 0.)
        _update(context)
        for n, frame in branch['frames'].items():
            c = frame.constraints[FOLLOW]
            m = np.asarray(branch['source'].pose.bones[n].matrix)
            rest = np.asarray(rig.data.bones[n].matrix_local)
            c.inverse_matrix = Matrix(np.linalg.solve(m, rest).tolist())
        branch['offset_signature'] = _offset_signature(branch['frames'])
        set_enabled(context, rig, True, evaluate=False)
        from . import natural_bind_reference
        undo_reference = natural_bind_reference.enable(context, rig, branch)
        _update(context)
    except BaseException:
        rollback()
        raise
    return rollback


def snapshot_offsets(rig):
    """Capture this solver's calibrated pose-space inverses for an explicit keyed frame."""
    rec = validate(rig)
    branch = active_branch(rec)
    result = {
        'version': 1,
        'token': rec['token'],
        'signature': branch['offset_signature'],
        'bones': {
            name: np.asarray(obj.constraints[FOLLOW].inverse_matrix).tolist()
            for name, obj in branch['frames'].items()
        },
    }
    if branch.get('kind'):
        result['solver'] = branch['kind']
        if branch.get('knee_plane'):
            result['knee_plane'] = branch['knee_plane']
    return result


def _snapshot_rows(rec, snapshot):
    if snapshot is None:
        return [(name, np.eye(4).tolist()) for name in sorted(rec['frames'].keys())]
    _require(
        snapshot['version'] == 1
        and snapshot['token'] == rec['token']
        and set(snapshot['bones']) == set(rec['frames']),
        'The keyed bind offsets belong to a different graph.',
    )
    rows = []
    for name, value in sorted(snapshot['bones'].items()):
        mat = np.asarray(value, dtype=np.float32)
        _require(
            mat.shape == (4, 4)
            and np.isfinite(mat).all()
            and np.allclose(mat[3], (0.0, 0.0, 0.0, 1.0))
            and abs(np.linalg.det(mat[:3, :3])) > 1.0e-10,
            'A keyed bind transform is invalid.',
        )
        rows.append((name, mat.tolist()))
    _require(_offset_hash(rows) == snapshot['signature'], 'The keyed bind offset data changed.')
    return rows


def _write_offsets(context, rec, rows, evaluate):
    signature = _offset_hash(rows)
    if signature == rec['offset_signature']:
        return
    old = {n: f.constraints[FOLLOW].inverse_matrix.copy() for n, f in rec['frames'].items()}
    previous = rec['offset_signature']
    try:
        for name, mat in rows:
            rec['frames'][name].constraints[FOLLOW].inverse_matrix = Matrix(mat)
        _require(
            _offset_signature(rec['frames']) == signature, 'Blender could not restore the keyed bind frame.'
        )
        rec['offset_signature'] = signature
        if evaluate:
            _update(context)
    except BaseException:
        for n, mat in old.items():
            rec['frames'][n].constraints[FOLLOW].inverse_matrix = mat
        rec['offset_signature'] = previous
        if evaluate:
            _update(context)
        raise


def restore_offsets(context, rig, snapshot, *, evaluate=False):
    """Restore a complete matrix set previously captured for this same bind graph."""
    rec = validate(rig)
    rows = _snapshot_rows(rec, snapshot)
    _keyed_route(context, rig, rec, snapshot, snapshot, rows, rows, 0., evaluate)


def _keyed_route(context, rig, rec, left, right, a, b, factor, evaluate):
    from . import natural_stable_solver
    stable = rec.get('stable')
    kinds = [(snapshot.get('solver', 'DIRECT') if snapshot else 'DIRECT') for snapshot in (left,right)]
    _require(all(k == 'DIRECT' or k == natural_stable_solver.KIND and stable for k in kinds),
             'A keyed Stable pose is missing its corresponding private solver.')
    if kinds[0] == kinds[1]:
        rows = a if factor == 0. else b if factor == 1. else [
            (n, np.asarray(Matrix(x).lerp(Matrix(y), factor)).tolist()) for (n,x), (_,y) in zip(a,b)]
        plans = [(stable if kinds[0] == natural_stable_solver.KIND else rec, rows)]
        mix = float(kinds[0] == natural_stable_solver.KIND)
    else:
        # The sources solve different mechanics. Blend their actual evaluated
        # transforms, each with its own offset in its own solver space.
        plans = [(stable if k == natural_stable_solver.KIND else rec, rows) for k, rows in zip(kinds, (a,b))]
        mix = factor if kinds[1] == natural_stable_solver.KIND else 1. - factor
    weights = [snapshot.get('knee_plane', 0.) if snapshot else 0. for snapshot in (left, right)]
    _require(all(isinstance(w, (int, float)) and 0. <= w <= 1. for w in weights),
             'Invalid keyed knee calibration.')
    if kinds[0] == kinds[1]:
        knee_weight = weights[0] + (weights[1] - weights[0]) * factor
    else:
        # At a Direct/Stable transition the outer COPY already blends the two poses.
        knee_weight = weights[kinds.index(natural_stable_solver.KIND)]
    rollback = []
    old_knee_weight = stable.get('knee_plane', 0.) if stable else 0.
    old_mix = stable['mix'] if stable else None
    try:
        for branch, rows in plans:
            if _offset_hash(rows) == branch['offset_signature']:
                continue
            old = [(n,np.asarray(f.constraints[FOLLOW].inverse_matrix).tolist()) for n,f in sorted(branch['frames'].items())]
            _write_offsets(context, branch, rows, False)
            rollback.append((branch, old))
        if stable:
            natural_stable_solver.set_knee_plane(rig, stable, knee_weight)
            natural_stable_solver.set_mix(rec, mix)
        if evaluate:
            _update(context)
    except BaseException:
        for branch, rows in rollback:
            _write_offsets(context, branch, rows, False)
        if stable:
            natural_stable_solver.set_knee_plane(rig, stable, old_knee_weight)
            natural_stable_solver.set_mix(rec, old_mix)
        if evaluate:
            _update(context)
        raise


def interpolate_offsets(context, rig, left, right, factor, *, evaluate=False):
    """Interpolate keyed bind corrections in the native solver's pose space. None is identity."""
    _require(0.0 <= factor <= 1.0, 'The bind interpolation weight is outside zero to one.')
    rec = validate(rig)
    _keyed_route(context, rig, rec, left, right, _snapshot_rows(rec,left), _snapshot_rows(rec,right), factor, evaluate)


def remove(context, rig, *, evaluate=True):
    rec = validate(rig)
    src, collection = rec['source'], rec['collection']
    _require(
        _solver_signature(src, rig) == rec['solver_signature'], 'The native solver now contains other edits.'
    )
    frames = dict(rec['frames'].items())
    _require(
        _rig_references(rig, rec) == list(rec['rig_references']),
        'Another reference on this rig uses a bind helper.',
    )
    _require(
        all(tuple(obj.users_collection) == (collection,) for obj in [src, *frames.values()])
        and src.data.users == 1,
        'The bind helpers have new owners.',
    )
    related = {rig, src, collection, *frames.values(), context.scene}
    if rec.get('neutral_source'):
        related.add(rec['neutral_source'])
    if rec.get('stable'):
        from . import natural_stable_solver
        related.update(natural_stable_solver.objects(rec['stable']))
    users = bpy.data.user_map(subset=set(frames.values()) | {src, collection, src.data})
    _require(
        all(u in related for group in users.values() for u in group),
        'Another datablock now uses a bind helper.',
    )
    if rec.get('stable'):
        natural_stable_solver.verify_owner(rig, rec)
    freeze_preview(rig, rec)
    if rec.get('stable'):
        natural_stable_solver.remove(rig, rec)
    state = dict(
        frames=frames,
        source=src,
        data=src.data,
        collection=collection,
        ik_rows=json.loads(rec['ik']),
        copies=[(rig.pose.bones[n], rig.pose.bones[n].constraints[COPY]) for n in frames]
        if rec['attached']
        else [],
    )
    del rig[KEY]
    _dispose(rig, state)
    if evaluate:
        _update(context)
