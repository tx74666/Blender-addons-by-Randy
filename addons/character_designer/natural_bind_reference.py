"""Evaluate the neutral native solver at the same IK/FK blend as the posed solver."""
import bpy
import numpy as np
from mathutils import Matrix

from . import natural_bind_graph as graph


REST = 'CD Original Bone Frame'
INVERSE = 'CD Neutral Solver Inverse'
POSE = 'CD Posed Solver Transform'
_ROUTES = (REST, INVERSE, POSE)
_AXES = tuple((i, j) for i in range(4) for j in range(4) if (i, j) != (3, 3))
_EQUAL = ' and '.join(f'p{i}{j}==n{i}{j}' for i, j in _AXES)


def _inputs(source, rig):
    """The posed solver's raw passthrough channels, excluding the IK and mechanism drivers."""
    for f in source.animation_data.drivers:
        driver = f.driver
        if driver.type != 'AVERAGE' or len(driver.variables) != 1:
            continue
        v = driver.variables[0]
        ref = v.targets[0]
        if (v.type == 'SINGLE_PROP' and ref.id == rig
                and ref.data_path == f.data_path + f'[{f.array_index}]'):
            yield f.data_path, f.array_index


def _freeze_inputs(reference, rig, paths):
    old = []
    try:
        for path, axis in paths:
            value = reference.path_resolve(path)
            new = rig.path_resolve(path)[axis]
            if value[axis] != new:
                old.append((path, axis, value[axis]))
                value[axis] = new
    except BaseException:
        _restore_inputs(reference, old)
        raise
    return old


def _restore_inputs(reference, old):
    for path, axis, value in old:
        reference.path_resolve(path)[axis] = value


def _input_signature(reference, paths):
    return graph._offset_hash(sorted((path, axis, reference.path_resolve(path)[axis]) for path, axis in paths))


def _equality_driver(constraint, name, source, reference):
    # These are Blender simple expressions. Include both affine edges of the
    # RNA matrix so translation is checked independently of indexing order.
    curve = constraint.driver_add('mute')
    curve.keyframe_points.clear()
    driver = curve.driver
    for i, j in _AXES:
        for prefix, arm in (('p', source), ('n', reference)):
            variable = driver.variables.new()
            variable.type = 'SINGLE_PROP'
            variable.name = f'{prefix}{i}{j}'
            target = variable.targets[0]
            target.id = arm
            target.data_path = arm.pose.bones[name].path_from_id('matrix') + f'[{i}][{j}]'
    driver.expression = _EQUAL


def _frames(rig, rec, reference):
    source = rec['source']
    for name, frame in rec['frames'].items():
        frame.constraints[graph.FOLLOW].mute = True
        # Store the full Rest affine on a constraint. Object.matrix_basis
        # would decompose it and lose small shear from the native bone chain.
        rest = frame.constraints.new('CHILD_OF')
        rest.name, rest.target = REST, reference
        rest.set_inverse_pending = False
        rest.inverse_matrix = rig.data.bones[name].matrix_local
        inv = graph._copy(frame, reference, '', INVERSE, 'CUSTOM', 'WORLD')
        inv.space_object, inv.space_subtarget = reference, name
        inv.mix_mode = 'BEFORE_FULL'
        pose = frame.constraints.new('CHILD_OF')
        pose.name, pose.target, pose.subtarget = POSE, source, name
        pose.target_space = 'POSE'
        pose.set_inverse_pending = False
        pose.inverse_matrix = Matrix.Identity(4)
        _equality_driver(inv, name, source, reference)
        _equality_driver(pose, name, source, reference)
        if rec.get('stable'):
            from . import natural_stable_solver
            frame.constraints.move(frame.constraints.find(natural_stable_solver.MIX), len(frame.constraints)-1)


def _discard(rig, rec, state):
    for frame in rec['frames'].values():
        for name in reversed(_ROUTES):
            constraint = frame.constraints.get(name)
            if constraint is not None:
                if name in (POSE, INVERSE):
                    constraint.driver_remove('mute')
                frame.constraints.remove(constraint)
        if frame.animation_data and not frame.animation_data.drivers:
            frame.animation_data_clear()
        frame.constraints[graph.FOLLOW].mute = False
    for key in ('neutral_source', 'neutral_signature', 'neutral_inputs'):
        if key in rec:
            del rec[key]
    if state.get('source'):
        bpy.data.objects.remove(state['source'], do_unlink=True)
    if state.get('data'):
        bpy.data.armatures.remove(state['data'])
    graph.refresh_references(rig, rec)


def enable(context, rig, rec):
    """Return an exact rollback for either a new reference or a new frozen input pose."""
    source = rec['source']
    reference = rec.get('neutral_source')
    paths = tuple(_inputs(source, rig))
    graph._require(bool(paths), 'The native bind solver has no registered control inputs.')
    if reference:
        verify_owner(rig, rec)
        old = _freeze_inputs(reference, rig, paths)
        previous = rec['neutral_inputs']
        def restore():
            _restore_inputs(reference, old)
            rec['neutral_inputs'] = previous
        try:
            rec['neutral_inputs'] = _input_signature(reference, paths)
            graph._update(context)
        except BaseException:
            restore()
            raise
        return restore
    state = {}
    try:
        reference = graph._source_object(source, rec['collection'], rec['token'], state)
        reference.name = reference.data.name = 'CD Neutral Limb Solver'
        for pb in reference.pose.bones:
            for c in pb.constraints:
                for field in ('target', 'pole_target', 'space_object'):
                    if getattr(c, field, None) == source:
                        setattr(c, field, reference)
        frozen = set(paths)
        for f in tuple(reference.animation_data.drivers):
            if (f.data_path, f.array_index) in frozen:
                reference.animation_data.drivers.remove(f)
                continue
            for v in f.driver.variables:
                for t in v.targets:
                    if t.id == source:
                        t.id = reference
        _freeze_inputs(reference, rig, paths)
        _frames(rig, rec, reference)
        rec['neutral_source'] = reference
        rec['neutral_signature'] = graph._solver_signature(reference, rig)
        rec['neutral_inputs'] = _input_signature(reference, paths)
        graph.refresh_references(rig, rec)
        graph._update(context)
        graph.validate(rig)
    except BaseException:
        _discard(rig, rec, state)
        graph._update(context)
        raise
    return lambda: _discard(rig, rec, state)


def validate_frame(rig, rec, frame, name):
    reference = rec['neutral_source']
    source = rec['source']
    rest, inv, pose = (frame.constraints.get(k) for k in _ROUTES)
    stack = list(frame.constraints)
    if rec.get('stable'):
        stack = stack[:-1]
    graph._require(rest and inv and pose and stack == [frame.constraints[graph.FOLLOW], rest, inv, pose]
                   and rest.type == 'CHILD_OF' and rest.target == reference and not rest.subtarget and not rest.mute
                   and tuple(map(tuple, rest.inverse_matrix)) == tuple(map(tuple, rig.data.bones[name].matrix_local))
                   and inv.type == 'COPY_TRANSFORMS' and inv.target == reference and not inv.subtarget
                   and inv.space_object == reference and inv.space_subtarget == name and inv.target_space == 'CUSTOM'
                   and inv.owner_space == 'WORLD' and inv.mix_mode == 'BEFORE_FULL' and not inv.remove_target_shear
                   and pose.type == 'CHILD_OF' and pose.target == source and pose.subtarget == name
                   and pose.target_space == 'POSE' and pose.owner_space == 'WORLD' and pose.inverse_matrix == Matrix.Identity(4)
                   and all(c.influence == 1. for c in (rest, inv, pose))
                   and all(getattr(c, 'use_' + kind + '_' + axis) for c in (rest, pose)
                           for kind in ('location','rotation','scale') for axis in 'xyz')
                   and frame.animation_data and not frame.animation_data.action and not frame.animation_data.nla_tracks
                   and len(frame.animation_data.drivers) == 2,
                   'The live neutral bind frame changed: ' + name)
    for c in (inv, pose):
        f = frame.animation_data.drivers.find(c.path_from_id('mute'))
        graph._require(f and f.driver.type == 'SCRIPTED' and not f.mute and not f.modifiers and not f.keyframe_points
                       and f.driver.expression == _EQUAL and len(f.driver.variables) == 2*len(_AXES),
                       'The neutral bind comparison changed: ' + name)
        for var, (i, j, prefix, arm) in zip(f.driver.variables, ((i,j,k,a) for i,j in _AXES for k,a in
                                                                 (('p',source),('n',reference)))):
            t = var.targets[0]
            graph._require(var.type == 'SINGLE_PROP' and var.name == f'{prefix}{i}{j}' and t.id == arm
                           and t.data_path == arm.pose.bones[name].path_from_id('matrix') + f'[{i}][{j}]',
                           'The neutral bind comparison input changed: ' + name)


def verify_owner(rig, rec):
    ref = rec['neutral_source']
    graph._require(ref and ref.type == 'ARMATURE' and ref != rig and ref != rec['source'] and
                   ref.get(graph.OWNER) == rec['token'] == ref.data.get(graph.OWNER) and
                   ref.parent is None and ref.matrix_basis == Matrix.Identity(4) and
                   not ref.constraints and not ref.modifiers and not ref.animation_data.action and
                   not ref.animation_data.nla_tracks and not ref.data.animation_data and
                   ref.data.users == 1 and tuple(ref.users_collection) == (rec['collection'],) and
                   graph._solver_signature(ref, rig) == rec['neutral_signature'] and
                   _input_signature(ref, _inputs(rec['source'], rig)) == rec['neutral_inputs'],
                   'The neutral limb solver was changed or acquired a different owner.')
    allowed = {rig, ref, rec['collection'], *rec['frames'].values()}
    # Blender also reports the scene owning the direct collection link.
    collection = rec['collection']
    allowed.update(scene for scene in rig.users_scene if scene.collection.children.get(collection.name) == collection)
    users = bpy.data.user_map(subset={ref, ref.data})
    graph._require(all(user in allowed for row in users.values() for user in row),
                   'Another datablock now uses the neutral limb solver.')


def prepare_freeze(rig, rec):
    """Validate ownership and solve all offsets before either solver is changed."""
    ref = rec['neutral_source']
    verify_owner(rig, rec)
    return {name: Matrix(np.linalg.solve(np.asarray(ref.pose.bones[name].matrix),
                                         np.asarray(rig.data.bones[name].matrix_local)).tolist())
            for name in rec['frames']}


def freeze(rig, rec, offsets=None):
    """Retain the current effective correction in the original keyed-offset format."""
    ref = rec.get('neutral_source')
    if not ref:
        return
    if offsets is None:
        offsets = prepare_freeze(rig, rec)
    _discard(rig, rec, {'source': ref, 'data': ref.data})
    for name, matrix in offsets.items():
        rec['frames'][name].constraints[graph.FOLLOW].inverse_matrix = matrix
    rec['offset_signature'] = graph._offset_signature(rec['frames'])
