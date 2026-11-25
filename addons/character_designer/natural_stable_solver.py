"""Roll-decoupled limb mechanisms shared by the Stable builder and private bind solvers."""
from contextlib import contextmanager
import math

import bpy
from mathutils import Matrix, Vector


KIND = 'STABLE_MCH_ORI'
MIX = 'CD Stable Solver Output'


def edit_mechanism(armature, name, source, parent, saved=None, *, connected=False):
    from . import limb_ik
    bone = armature.data.edit_bones.new(name)
    if bone.name != name:
        armature.data.edit_bones.remove(bone)
        raise limb_ik.LimbIKError(f"Blender renamed generated bone '{name}'.")
    saved = saved or {}
    try:
        bone.head = Vector(saved.get('head', source.head))
        bone.tail = Vector(saved.get('tail', source.tail))
        if 'z' in saved:
            bone.align_roll(Vector(saved['z']))
        else:
            bone.roll = source.roll
        bone.parent = parent
        bone.use_connect = connected
        bone.use_deform = False
    except BaseException:
        armature.data.edit_bones.remove(bone)
        raise
    return bone


def orientation_constraints(armature, rig_id, segment, source, mechanism_name, orientation_name, created):
    from . import limb_ik
    orientation = armature.pose.bones[orientation_name]
    result = []
    for owner, kind, subtarget, role in (
            (orientation, 'COPY_LOCATION', mechanism_name, f'ORI_{segment}_LOCATION'),
            (orientation, 'DAMPED_TRACK', mechanism_name, f'ORI_{segment}_TRACK'),
            (source, 'COPY_ROTATION', orientation_name, f'SOURCE_{segment}_ROTATION')):
        constraint = owner.constraints.new(type=kind)
        created.append((owner, constraint))
        constraint.name = limb_ik._constraint_name(rig_id, role)
        constraint.target = armature
        constraint.subtarget = subtarget
        constraint.target_space = constraint.owner_space = 'WORLD'
        if kind == 'COPY_LOCATION':
            constraint.head_tail = 0.
        elif kind == 'DAMPED_TRACK':
            constraint.head_tail = 1.
            constraint.track_axis = 'TRACK_Y'
        else:
            constraint.mix_mode = 'REPLACE'
        result.append((owner, constraint, role))
    return result


@contextmanager
def _editing_copy(context, source):
    # An independent scene/view layer makes only the private copy editable.
    # The artist's Armature never exits Pose Mode; its bone selection is untouched.
    work = bpy.data.scenes.new('CD Solver Construction')
    try:
        work.collection.objects.link(source)
        layer = work.view_layers[0]
        with context.temp_override(scene=work, view_layer=layer, object=source, active_object=source,
                                   selected_objects=[source], selected_editable_objects=[source]):
            layer.objects.active = source
            source.hide_set(False, view_layer=layer)
            source.select_set(True, view_layer=layer)
            if bpy.ops.object.mode_set(mode='EDIT') != {'FINISHED'}:
                raise ValueError('Could not edit the private Stable solver.')
            try:
                yield source.data.edit_bones
            finally:
                bpy.ops.object.mode_set(mode='OBJECT')
    finally:
        bpy.data.scenes.remove(work)


def _retarget(clone, old):
    for pb in clone.pose.bones:
        for c in pb.constraints:
            for field in ('target', 'pole_target', 'space_object'):
                if getattr(c, field, None) == old:
                    setattr(c, field, clone)
    for f in clone.animation_data.drivers:
        for v in f.driver.variables:
            for t in v.targets:
                if t.id == old:
                    t.id = clone


def clone(context, rig, rec, inventory, state):
    """Build a private, full-strength mechanical IK with blended native FK outputs."""
    from . import limb_ik, limb_ik_fk, natural_bind_graph as graph
    source = graph._source_object(rec['source'], rec['collection'], rec['token'], state)
    source.name = source.data.name = 'CD Stable Limb Solver'
    _retarget(source, rec['source'])
    rows = []
    with _editing_copy(context, source) as edit:
        for (kind, side), entry in inventory['rigs'].items():
            names = {role: limb_ik.LIMB_SPEC[kind][role].format(side=side)
                     for role in ('mch_upper', 'mch_lower', 'ori_upper', 'ori_lower')}
            upper, lower = (edit[n] for n in entry['chain'][:2])
            anchor = upper.parent or edit.get(limb_ik.MASTER_NAME)
            a = edit_mechanism(source, names['mch_upper'], upper, anchor)
            edit_mechanism(source, names['mch_lower'], lower, a, connected=True)
            edit_mechanism(source, names['ori_upper'], upper, anchor)
            edit_mechanism(source, names['ori_lower'], lower, anchor)
            rows.append((entry, names))
    for entry, names in rows:
        pb, original, record = next(row for row in entry['entries'] if row[2]['role'] == 'IK')
        owner = source.pose.bones[pb.name]
        direct = owner.constraints[original.name]
        # Keep X's actual registered target, pole and calibrated pole angle.
        # Both mechanical Rest frames are exact copies of those same bones.
        ik = source.pose.bones[names['mch_lower']].constraints.new('IK')
        ik.name = direct.name
        ik.target = ik.pole_target = source
        ik.subtarget, ik.pole_subtarget = direct.subtarget, direct.pole_subtarget
        ik.pole_angle = direct.pole_angle
        ik.chain_count = 2
        ik.use_tail, ik.use_stretch, ik.use_rotation = True, False, False
        ik.target_space = ik.owner_space = 'WORLD'
        direct.driver_remove('influence')
        owner.constraints.remove(direct)
        for i, segment in enumerate(('UPPER','LOWER')):
            lower = segment.lower()
            native = source.pose.bones[entry['chain'][i]]
            relations = orientation_constraints(source, entry['rig_id'], segment, native,
                                                 names['mch_'+lower], names['ori_'+lower], [])
            rotation = relations[-1][1]
            curve = rotation.driver_add('influence')
            curve.keyframe_points.clear()
            driver = curve.driver
            driver.type = 'AVERAGE'
            var = driver.variables.new()
            var.type = 'SINGLE_PROP'
            var.targets[0].id = rig
            var.targets[0].data_path = limb_ik_fk.property_path(rig.pose.bones[entry['target'].name])
    from . import foot_roll_hinge_stable
    for entry in inventory['rigs'].values():
        foot = entry.get('foot_controls')
        if foot and foot.get('roll_hinge') == 1:
            foot_roll_hinge_stable.replace_cloned_reference(context, source, rig, foot)
    graph._update(context)
    return source


def objects(branch):
    result = {branch['source'], *branch['frames'].values()}
    if branch.get('neutral_source'):
        result.add(branch['neutral_source'])
    return result


def references(branch):
    result = [branch['source'], branch['source'].data]
    if branch.get('neutral_source'):
        result.extend((branch['neutral_source'], branch['neutral_source'].data))
    result.extend(obj for name, obj in sorted(branch['frames'].items()))
    return result


def set_mix(rec, value):
    """A zero contribution bypasses the extra native copy entirely on legacy keys."""
    for frame in rec['frames'].values():
        constraint = frame.constraints[MIX]
        constraint.influence, constraint.mute = value, value == 0.
    rec['stable']['mix'] = constraint.influence


def _calibrate_knee(context, source, ik, shin, probe):
    """Measure the mechanical bend plane with the ankle well inside the reachable sphere."""
    from . import limb_ik
    upper = shin.parent
    pole = source.pose.bones[ik.pole_subtarget]
    target, subtarget, start_angle = ik.target, ik.subtarget, ik.pole_angle
    try:
        axis = (shin.tail - upper.head).normalized()
        length = upper.bone.length + shin.bone.length
        probe.location = source.matrix_world @ (upper.head + axis * (0.75 * length))
        ik.target = probe
        ik.subtarget = ''
        for attempt in range(6):
            context.view_layer.update()
            axis = shin.tail - upper.head
            error = limb_ik._signed_angle(pole.head - upper.head, shin.head - upper.head, axis)
            if abs(error) < 0.0002:
                return ik.pole_angle
            # Blender twists its IK plane around the root-to-ankle axis as pole_angle grows.
            ik.pole_angle = math.remainder(ik.pole_angle - error, math.tau)
        raise ValueError('Could not align the bent knee with ' + pole.name)
    finally:
        ik.target, ik.subtarget, ik.pole_angle = target, subtarget, start_angle
        context.view_layer.update()


def set_knee_plane(rig, branch, weight):
    """Old action snapshots use zero; the next explicit body reset opts into alignment."""
    if branch.get('knee_plane', 0.) != weight:
        if 'knee_plane' not in branch:
            raise ValueError('This keyed pose needs its calibrated knee solver.')
        branch['knee_plane'] = weight
        rig.update_tag()


def align_knees(context, rig, branch, inventory):
    from . import limb_ik, natural_bind_graph as graph, natural_bind_reference as neutral
    if 'knee_plane' in branch:
        old = branch['knee_plane']
        set_knee_plane(rig, branch, 1.)
        return lambda: set_knee_plane(rig, branch, old)
    source = branch['source']
    reference = branch.get('neutral_source')
    if reference:
        neutral.verify_owner(rig, branch)
    helpers = [(source, 'solver_signature')]
    if reference:
        helpers.append((reference, 'neutral_signature'))
    signatures = {key: branch[key] for _, key in helpers}
    changes = []

    def rollback():
        for ik, previous in reversed(changes):
            ik.driver_remove('pole_angle')
            ik.pole_angle = previous
        if 'knee_plane' in branch:
            del branch['knee_plane']
        for key, value in signatures.items():
            branch[key] = value
        context.view_layer.update()

    probe = bpy.data.objects.new('CD Knee Calibration', None)
    try:
        context.scene.collection.objects.link(probe)
        branch['knee_plane'] = 1.
        # Evaluate the new driver input before sampling either existing solver.
        rig.update_tag()
        context.view_layer.update()
        for side in ('L', 'R'):
            entry = inventory['rigs']['LEG', side]
            constraint_name = next(c.name for _, c, row in entry['entries'] if row['role'] == 'IK')
            shin_name = limb_ik.LIMB_SPEC['LEG']['mch_lower'].format(side=side)
            shin = source.pose.bones[shin_name]
            ik = shin.constraints[constraint_name]
            original = ik.pole_angle
            aligned = _calibrate_knee(context, source, ik, shin, probe)
            delta = math.remainder(aligned - original, math.tau)
            for obj, _ in helpers:
                ik = obj.pose.bones[shin_name].constraints[constraint_name]
                if ik.pole_angle != original or obj.animation_data.drivers.find(ik.path_from_id('pole_angle')):
                    raise ValueError('The private knee calibration no longer matches its neutral reference.')
                curve = ik.driver_add('pole_angle')
                changes.append((ik, original))
                curve.keyframe_points.clear()
                driver = curve.driver
                variable = driver.variables.new()
                variable.name = 'weight'
                variable.type = 'SINGLE_PROP'
                variable.targets[0].id = rig
                variable.targets[0].data_path = f'["{graph.KEY}"]["stable"]["knee_plane"]'
                value = f'({original!r} + weight * {delta!r})'
                driver.type = 'SCRIPTED'
                driver.expression = f'atan2(sin({value}), cos({value}))'
        for obj, key in helpers:
            branch[key] = graph._solver_signature(obj, rig)
        context.view_layer.update()
    except BaseException:
        rollback()
        raise
    finally:
        bpy.data.objects.remove(probe, do_unlink=True)
    return rollback


def _dispose(rig, rec, state):
    from . import natural_bind_graph as graph
    for frame in rec['frames'].values():
        c = frame.constraints.get(MIX)
        if c is not None:
            frame.constraints.remove(c)
    if 'stable' in rec:
        del rec['stable']
    for obj in state.get('frames', {}).values():
        bpy.data.objects.remove(obj, do_unlink=True)
    if state.get('source'):
        bpy.data.objects.remove(state['source'], do_unlink=True)
    if state.get('data'):
        bpy.data.armatures.remove(state['data'])
    graph.refresh_references(rig, rec)


def attach(context, rig, rec, inventory):
    from . import natural_bind_graph as graph
    if rec.get('stable'):
        verify_owner(rig, rec)
        return None
    graph._require(not any(frame.constraints.get(MIX) for frame in rec['frames'].values()),
                   'A Stable solver output name is already in use.')
    state = {'frames': {}}
    try:
        source = clone(context, rig, rec, inventory, state)
        graph._relative(context, rig, source, rec['collection'], rec['frames'].keys(), rec['token'], state)
        for name, output in state['frames'].items():
            output.name = 'CD Stable Bind ' + name
            graph._copy(rec['frames'][name], output, '', MIX, 'WORLD', 'WORLD')
        rec['stable'] = {
            'kind': KIND, 'token': rec['token'], 'source': source, 'collection': rec['collection'],
            'frames': state['frames'], 'solver_signature': graph._solver_signature(source, rig),
            'offset_signature': graph._offset_signature(state['frames']), 'mix': 1.,
        }
        graph.refresh_references(rig, rec['stable'])
    except BaseException:
        _dispose(rig, rec, state)
        raise
    return lambda: _dispose(rig, rec, state)


def validate(rig, rec):
    from . import natural_bind_graph as g, natural_bind_reference as neutral
    branch = rec['stable']
    src = branch['source']
    ref = branch.get('neutral_source')
    g._require(branch['kind'] == KIND and branch['token'] == rec['token']
               and branch['collection'] == rec['collection']
               and src != rec['source'] and src.parent is None and src.matrix_basis == Matrix.Identity(4)
               and src.get(g.OWNER) == branch['token'] == src.data.get(g.OWNER)
               and not src.modifiers and not src.constraints and not src.animation_data.action
               and not src.animation_data.nla_tracks and not src.data.animation_data
               and set(rec['frames']) == set(branch['frames']) and 0 <= branch['mix'] <= 1.,
               'The private Stable solver was changed.')
    for name, output in branch['frames'].items():
        frame = rec['frames'][name]
        mix = frame.constraints.get(MIX)
        follow = output.constraints.get(g.FOLLOW)
        g._require(mix and mix == frame.constraints[-1] and mix.type == 'COPY_TRANSFORMS'
                   and mix.target == output and not mix.subtarget and mix.mix_mode == 'REPLACE'
                   and mix.owner_space == mix.target_space == 'WORLD' and not mix.remove_target_shear
                   and mix.influence == branch['mix'] and mix.mute == (branch['mix'] == 0.)
                   and output.type == 'EMPTY' and output.get(g.OWNER) == branch['token']
                   and output.parent is None and output.matrix_basis == Matrix.Identity(4)
                   and len(output.constraints) == (4 if ref else 1) and (ref or not output.animation_data)
                   and follow and follow.type == 'CHILD_OF' and follow.target == src and follow.subtarget == name
                   and follow.target_space == 'POSE' and follow.owner_space == 'WORLD'
                   and follow.mute == bool(ref) and follow.influence == 1.
                   and all(getattr(follow, 'use_'+kind+'_'+axis) for kind in ('location','rotation','scale') for axis in 'xyz'),
                   'A private Stable output changed: ' + name)
        if ref:
            neutral.validate_frame(rig, branch, output, name)
    g._require(branch['offset_signature'] == g._offset_signature(branch['frames']),
               'A private Stable keyed offset was changed.')


def verify_owner(rig, rec):
    from . import natural_bind_graph as graph
    branch = rec['stable']
    source = branch['source']
    owned = objects(branch)
    collection = rec['collection']
    graph._require(all(tuple(obj.users_collection) == (collection,) for obj in owned)
                   and source.data.users == 1 and graph._solver_signature(source, rig) == branch['solver_signature'],
                   'The Stable solver was edited or has another owner.')
    allowed = {rig, collection, *owned, *rec['frames'].values()}
    allowed.update(scene for scene in rig.users_scene if scene.collection.children.get(collection.name) == collection)
    users = bpy.data.user_map(subset=owned | {source.data})
    graph._require(all(user in allowed for row in users.values() for user in row),
                   'Another datablock uses the Stable solver.')


def remove(rig, rec):
    """Remove the private branch after the complete graph passed removal preflight."""
    branch = rec['stable']
    source = branch['source']
    _dispose(rig, rec, {'frames': dict(branch['frames'].items()), 'source': source, 'data': source.data})
