"""World-space character placement without editing its skeleton or hierarchy."""
import math
import struct
from array import array

import bpy
from bpy.props import FloatProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Matrix, Vector


_TRANSFORMS = frozenset({
    'location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle',
    'scale', 'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion',
    'delta_scale', 'matrix_basis', 'matrix_local', 'matrix_world',
    'matrix_parent_inverse', 'parent', 'parent_type', 'parent_bone',
})
_CHANNELS = ('location', 'rotation_euler', 'rotation_quaternion',
             'rotation_axis_angle', 'scale', 'delta_location',
             'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')
_POSE_CHANNELS = ('location', 'rotation_euler', 'rotation_quaternion',
                  'rotation_axis_angle', 'scale')
_ERRORS = (ValueError, RuntimeError, TypeError, KeyError, ReferenceError)
_HIDDEN_LOCAL_MODIFIERS = frozenset({
    'ARMATURE', 'SUBSURF', 'MULTIRES', 'MIRROR', 'TRIANGULATE',
    'SOLIDIFY', 'BEVEL', 'WELD', 'EDGE_SPLIT', 'WEIGHTED_NORMAL',
})

_GROUND_PRECISION = 1e-6
_GROUND_NEIGHBOR_STEPS = 8


class _ShapeChangeError(ValueError):
    def __init__(self, mesh, message):
        super().__init__(message)
        self.mesh = mesh


class _FloorPrecisionError(ValueError):
    pass


def _mesh_numerical_limit(points):
    """Bound native evaluated-coordinate noise by the model's own size.

    Native Hook + Spline IK can amplify float32 coordinate cancellation during
    a world translation even with unchanged curve inputs and bone channels.
    This bound applies only to evaluated points, never authored Rest/channels.
    """
    diagonal = math.sqrt(sum((max(points[axis::3]) - min(points[axis::3])) ** 2
                             for axis in range(3))) if points else 0.
    return diagonal, 2e-5 + diagonal * 1e-4


def _matrix(matrix):
    return tuple(tuple(row) for row in matrix)


def _close(actual, expected, tolerance=2e-5):
    return all(abs(a - b) <= tolerance + 5e-7 * max(abs(a), abs(b))
               for ra, re in zip(actual, expected) for a, b in zip(ra, re))


def _is_transform(path):
    return any(path == name or path.startswith(name + '[') for name in _TRANSFORMS)


def _require(context, rig):
    from . import animation_worklist_target, skirt_rig
    if (rig is None or rig.type != 'ARMATURE' or context.mode not in {'OBJECT', 'POSE'}
            or rig.library or rig.override_library or not rig.is_editable
            or rig.name not in context.scene.objects):
        raise ValueError('Choose a local Main Rig in Object or Pose Mode.')
    if rig.get(skirt_rig.OWNER_KEY):
        raise ValueError('Choose the Main Rig to move the whole character.')
    if animation_worklist_target.is_adopted(rig):
        raise ValueError('Restore Main Rig in Animation > Advanced before changing character placement; the active editing session owns its saved model position.')
    if context.screen and context.screen.is_animation_playing:
        raise ValueError('Pause playback before moving the character.')


def _scene_less_subtree(obj):
    """Ignore only branches with no scene membership anywhere below them."""
    pending, seen = [obj], set()
    while pending:
        item = pending.pop()
        if item in seen:
            continue
        seen.add(item)
        if item.users_scene:
            return False
        pending.extend(item.children)
    return True


def _scope(context, rig):
    from . import bone_display
    rigs = {rig} | {item for item, _record in bone_display.dress_rigs(context, rig)}
    objects = set(rigs)
    for obj in context.scene.objects:
        if obj.type == 'MESH' and any(m.type == 'ARMATURE' and m.object in rigs for m in obj.modifiers):
            objects.add(obj)
    # Include every descendant, so a bone-parented accessory follows exactly
    # once. No new parenting or guessed sibling collection ownership is used.
    pending = list(objects)
    while pending:
        parent = pending.pop()
        for child in parent.children:
            if child.name not in context.scene.objects:
                if _scene_less_subtree(child):
                    continue
                raise ValueError(f"Character child '{child.name}' is outside the current scene but its hierarchy contains scene objects; keep that placement intact.")
            if child not in objects:
                objects.add(child)
                pending.append(child)
    for obj in objects:
        if (obj.library or obj.override_library or not obj.is_editable
                or set(obj.users_scene) != {context.scene}):
            raise ValueError(f'{obj.name} is linked, overridden or shared between scenes; preserve that placement first.')
        if obj.type == 'ARMATURE' and obj not in rigs:
            raise ValueError(f'{obj.name} is an unrecognized child rig; register its character attachment first.')
    roots = [obj for obj in objects if obj.parent not in objects]
    return objects, rigs, sorted(roots, key=lambda obj: obj.name)


def _external_targets(owner, scope):
    for con in owner.constraints:
        if con.mute or con.influence == 0:
            continue
        targets = []
        for name in ('target', 'pole_target', 'space_object'):
            value = getattr(con, name, None)
            if isinstance(value, bpy.types.Object):
                targets.append(value)
        targets.extend(t.target for t in getattr(con, 'targets', ()) if t.target)
        if any(target not in scope for target in targets):
            raise ValueError(f'{owner.name} has a constraint using an external transform; preserve that dependency before placement.')
        if not targets and getattr(con, 'owner_space', None) == 'WORLD':
            raise ValueError(f'{owner.name} has a world-space constraint that may change its internal pose during placement.')


def _admit(context, objects, rigs, roots):
    from . import limb_ik
    roots = set(roots)
    for obj in objects:
        if obj.constraints:
            raise ValueError(f'{obj.name} has object constraints; preserve those transforms before character placement.')
        if obj in roots:
            for action in limb_ik._actions_for_id(obj):
                if any(_is_transform(curve.data_path) for curve in limb_ik._fcurves_for_action(action)):
                    raise ValueError(f'{obj.name} has object transform animation; preserve it before character placement.')
        animation = obj.animation_data
        for curve in animation.drivers if animation else ():
            if obj in roots and _is_transform(curve.data_path):
                raise ValueError(f'{obj.name} has an object transform driver; preserve it before character placement.')
        if obj.type == 'ARMATURE':
            for pb in obj.pose.bones:
                _external_targets(pb, objects)
        for owner in (obj, obj.data, getattr(obj.data, 'shape_keys', None)):
            animation = getattr(owner, 'animation_data', None)
            for curve in animation.drivers if animation else ():
                for variable in curve.driver.variables:
                    for target in variable.targets:
                        target_id = target.id
                        if isinstance(target_id, bpy.types.Object) and target_id not in objects and (
                                variable.type in {'TRANSFORMS', 'LOC_DIFF', 'ROTATION_DIFF'}
                                or _is_transform(target.data_path)
                                or target.data_path.startswith('pose.bones[')):
                            raise ValueError(f'{obj.name} has a driver reading an external transform; preserve that dependency first.')
        for modifier in obj.modifiers:
            if not modifier.show_viewport:
                continue
            for prop in modifier.bl_rna.properties:
                if prop.type != 'POINTER':
                    continue
                target = getattr(modifier, prop.identifier, None)
                if isinstance(target, bpy.types.Object) and target not in objects:
                    raise ValueError(f'{obj.name} uses the external modifier target {target.name}; keep that dependency intact.')
    for root in roots:
        if root.parent and root.parent_type != 'OBJECT':
            raise ValueError('A character root follows an external bone or vertex; preserve that attachment before placement.')
        if abs(root.matrix_world.to_3x3().determinant()) < 1e-12:
            raise ValueError(f'{root.name} has a zero-scale world transform; character placement cannot invert it.')


def _excluded_meshes(context, objects):
    from . import reference_views, widget_collections
    excluded = {pb.custom_shape for obj in bpy.data.objects if obj.type == 'ARMATURE'
                for pb in obj.pose.bones if pb.custom_shape}
    parents = {}
    for collection in bpy.data.collections:
        for child in collection.children:
            parents.setdefault(child, set()).add(collection)

    def excluded_collection(collection, seen=None):
        seen = set() if seen is None else seen
        if collection in seen:
            return False
        seen.add(collection)
        base_name = collection.name.casefold().split('.')[0]
        if (base_name in {'backup', 'backups'}
                or collection.get('character_designer_ground_exclude')
                or collection.get(widget_collections.OWNER_KEY) == widget_collections.OWNER_VALUE
                or str(collection.get('character_designer_role', '')).startswith('WIDGET')
                or collection.get(reference_views._GENERATOR_KEY) == reference_views.REFERENCE_GENERATOR_ID):
            return True
        return any(excluded_collection(parent, seen) for parent in parents.get(collection, ()))

    for obj in objects:
        if (obj.get('character_designer_ground_exclude')
                or str(obj.get('character_designer_role', '')).startswith('WIDGET')
                or obj.get(reference_views._GENERATOR_KEY) == reference_views.REFERENCE_GENERATOR_ID
                or any(excluded_collection(collection) for collection in obj.users_collection)):
            excluded.add(obj)
    return excluded


def _points(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        result = array('d')
        world = evaluated.matrix_world
        for vertex in mesh.vertices:
            point = world @ vertex.co
            if not all(math.isfinite(value) for value in point):
                raise ValueError(f'{obj.name} has a non-finite evaluated vertex.')
            result.extend(point)
        return result
    finally:
        evaluated.to_mesh_clear()


def _channels(obj):
    return {'rotation_mode': obj.rotation_mode,
            'channels': {name: tuple(getattr(obj, name)) for name in _CHANNELS},
            'parent_inverse': _matrix(obj.matrix_parent_inverse)}


def _rig_state(rig):
    return (rig.data.pose_position,
            tuple((bone.name, _matrix(bone.matrix_local), tuple(bone.head_local), tuple(bone.tail_local),
                   bone.parent.name if bone.parent else None, bone.use_connect, bone.use_deform)
                  for bone in rig.data.bones),
            tuple((pb.name, pb.rotation_mode, tuple((name, tuple(getattr(pb, name))) for name in _POSE_CHANNELS),
                   _matrix(pb.matrix_basis), pb.custom_shape, pb.custom_shape_transform)
                  for pb in rig.pose.bones))


def _admit_hidden_meshes(meshes, visible, objects):
    """Hidden graph outputs alone cannot prove world-dependent shapes safe.

    A hidden/excluded object may have an unevaluated or cached graph output.
    Admit only explicitly translation-invariant local modifier stacks, with no
    native drivers capable of changing their inputs. Object modifier targets
    have already been restricted to the translating character by _admit.
    Showing a rejected mesh routes it through the ordinary evaluated proof.
    """
    hidden = [obj for obj in meshes if obj not in visible]
    for obj in hidden:
        if obj in visible:
            continue
        for modifier in obj.modifiers:
            if modifier.show_viewport and modifier.type not in _HIDDEN_LOCAL_MODIFIERS:
                raise ValueError(
                    f'Hidden mesh {obj.name} uses {modifier.type}; show this mesh before character placement so its evaluated shape can be verified.')
        for owner in (obj, obj.data, obj.data.shape_keys):
            animation = getattr(owner, 'animation_data', None)
            if animation and animation.drivers:
                raise ValueError(
                    f'Hidden mesh {obj.name} has driven inputs; show this mesh before character placement so its evaluated shape can be verified.')
    if not hidden:
        return
    # A driver's world-position input on the rig or another scoped object can
    # alter a hidden mesh indirectly, even when that mesh has no own drivers.
    # Generated IK/FK drivers reading pose-local properties remain admitted.
    for obj in objects:
        for owner in (obj, obj.data, getattr(obj.data, 'shape_keys', None)):
            animation = getattr(owner, 'animation_data', None)
            for curve in animation.drivers if animation else ():
                for variable in curve.driver.variables:
                    for target in variable.targets:
                        if target.id not in objects:
                            continue
                        world_position = (variable.type == 'TRANSFORMS'
                                          and target.transform_space == 'WORLD_SPACE'
                                          and target.transform_type.startswith('LOC_'))
                        object_channels = variable.type == 'SINGLE_PROP' and _is_transform(target.data_path)
                        if world_position or object_channels:
                            raise ValueError(
                                'Hidden character meshes depend on driven world/object transforms; show these meshes before character placement so their evaluated shapes can be verified.')


def _prepare(context, rig):
    _require(context, rig)
    context.view_layer.update()
    objects, rigs, roots = _scope(context, rig)
    _admit(context, objects, rigs, roots)
    excluded = _excluded_meshes(context, objects)
    meshes = sorted((obj for obj in objects if obj.type == 'MESH' and obj not in excluded),
                    key=lambda obj: obj.name)
    visible = {obj for obj in meshes if obj.visible_get(view_layer=context.view_layer)}
    if not visible:
        raise ValueError('No visible character mesh was found; show the body or shoes before placement.')
    _admit_hidden_meshes(meshes, visible, objects)
    depsgraph = context.evaluated_depsgraph_get()
    points = {obj: _points(obj, depsgraph) for obj in meshes}
    lowest = min((min(values[2::3]) for obj, values in points.items() if obj in visible and values), default=None)
    if lowest is None:
        raise ValueError('The visible character meshes contain no evaluated vertices.')
    return {'objects': objects, 'rigs': rigs, 'roots': roots, 'points': points, 'visible': visible, 'lowest': lowest,
            'world': {obj: obj.evaluated_get(depsgraph).matrix_world.copy() for obj in objects},
            'channels': {obj: _channels(obj) for obj in objects},
            'internal': {item: _rig_state(item) for item in rigs}}


def _restore_roots(state):
    for root in state['roots']:
        before = state['channels'][root]
        root.rotation_mode = before['rotation_mode']
        for name, value in before['channels'].items():
            setattr(root, name, value)
        root.matrix_parent_inverse = Matrix(before['parent_inverse'])


def _tag_scope(objects):
    # Object-only movement does not reliably dirty native Curve path caches
    # used by Spline IK. Refresh the complete translating graph, including its
    # local data evaluation, without changing geometry or pose inputs.
    for obj in objects:
        if obj.type in {'ARMATURE', 'CURVE', 'MESH'}:
            obj.update_tag(refresh={'OBJECT', 'DATA'})
            obj.data.update_tag()


def _verify(context, state, distance):
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    translation = Matrix.Translation((0., 0., distance))
    for obj, before in state['world'].items():
        if not _close(obj.evaluated_get(depsgraph).matrix_world, translation @ before):
            raise ValueError(f'{obj.name} did not move exactly once with the character.')
        channels = _channels(obj)
        old = state['channels'][obj]
        if channels['rotation_mode'] != old['rotation_mode'] or channels['parent_inverse'] != old['parent_inverse']:
            raise ValueError(f'{obj.name} changed its internal transform configuration.')
        for name, value in old['channels'].items():
            if name != 'location' or obj not in state['roots']:
                if channels['channels'][name] != value:
                    raise ValueError(f'{obj.name} changed an internal transform channel.')
    for rig, before in state['internal'].items():
        if _rig_state(rig) != before:
            raise ValueError(f'{rig.name} changed its original bones or local pose during placement.')
    lowest = None
    mesh_proof = {}
    for obj, before in state['points'].items():
        after = _points(obj, depsgraph)
        if len(after) != len(before):
            raise ValueError(f'{obj.name} changed its evaluated topology during placement.')
        diagonal, tolerance = _mesh_numerical_limit(before)
        max_error = 0.
        for index, (old, new) in enumerate(zip(before, after)):
            expected = old + (distance if index % 3 == 2 else 0.)
            error = abs(new - expected)
            max_error = max(max_error, error)
            if error > tolerance:
                raise _ShapeChangeError(obj, f'{obj.name} changed its shape during character placement '
                                 f'(vertex {index // 3}, axis {"XYZ"[index % 3]}, '
                                 f'difference {new - expected:.9g}, native limit {tolerance:.9g}).')
        mesh_proof[obj.name] = {'bbox_diagonal': diagonal, 'tolerance': tolerance,
                                'max_abs_world_component_error': max_error,
                                'visible': obj in state['visible']}
        if after and obj in state['visible']:
            value = min(after[2::3])
            lowest = value if lowest is None else min(lowest, value)
    state['mesh_proof'] = mesh_proof
    state['lowest_tolerance'] = max((item['tolerance'] for item in mesh_proof.values() if item['visible']), default=0.)
    if lowest is None or abs(lowest - (state['lowest'] + distance)) > state['lowest_tolerance']:
        raise ValueError('The character bottom did not reach its expected height within the native numerical limit.')
    return lowest


def _translate(context, state, distance, *, floor_state=None, offset_state=None,
               stable_floor=False):
    if not math.isfinite(distance):
        raise ValueError('Enter a finite vertical distance.')
    delta = Vector((0., 0., distance))
    offsets = {}
    for root in state['roots']:
        # W = parent_frame @ basis. Change only location, preserving every
        # rotation/scale channel even below a rotated, nonuniform parent.
        local_delta = root.matrix_basis.to_3x3() @ state['world'][root].to_3x3().inverted() @ delta
        if any(locked and abs(value) > 1e-8 for locked, value in zip(root.lock_location, local_delta)):
            raise ValueError(f'Unlock the required location axes on {root.name} before placement.')
        offsets[root] = local_delta
    from . import body_setup, placement_offsets
    verified = state
    if offset_state is not None:
        verified = dict(state)
        verified['channels'] = dict(state['channels'])
        for obj, planned in offset_state['expected_channels'].items():
            before = state['channels'][obj]
            verified['channels'][obj] = {**before, 'channels': {**before['channels'], **planned}}
    with body_setup._runtime_paused():
        try:
            if offset_state is not None:
                placement_offsets.apply(context, offset_state)
            for root, offset in offsets.items():
                root.location = Vector(state['channels'][root]['channels']['location']) + offset
            _tag_scope(state['objects'])
            floor_proof = None
            if floor_state is not None:
                from . import floor_widgets
                floor_widgets.apply(context, floor_state)
            lowest = _verify(context, verified, distance)
            if stable_floor:
                # A nearby representable floor position must survive another
                # complete native graph rebuild against the original inputs.
                if abs(lowest) > _GROUND_PRECISION:
                    raise _FloorPrecisionError('The character bottom exceeds the stable floor precision.')
                _tag_scope(state['objects'])
                lowest = _verify(context, verified, distance)
                if abs(lowest) > _GROUND_PRECISION:
                    raise _FloorPrecisionError('The rebuilt character bottom exceeds the stable floor precision.')
            if floor_state is not None:
                floor_proof = floor_widgets.verify(context, floor_state)
            offset_proof = placement_offsets.verify(context, offset_state) if offset_state is not None else None
        except Exception as original:
            recovery_errors = []
            if floor_state is not None:
                from . import floor_widgets
                try:
                    floor_widgets.restore(context, floor_state)
                except Exception as exc:
                    recovery_errors.append(exc)
            try:
                _restore_roots(state)
            except Exception as exc:
                recovery_errors.append(exc)
            if offset_state is not None:
                try:
                    placement_offsets.restore(context, offset_state)
                except Exception as exc:
                    recovery_errors.append(exc)
            try:
                _tag_scope(state['objects'])
                context.view_layer.update()
            except Exception as exc:
                recovery_errors.append(exc)
            for root in state['roots']:
                if _channels(root) != state['channels'][root]:
                    recovery_errors.append(RuntimeError(f'{root.name} local transform was not restored.'))
            if recovery_errors:
                raise RuntimeError('Character placement failed and recovery was incomplete; use Undo. '
                                   + '; '.join(str(exc) for exc in recovery_errors)) from original
            raise
    return {'distance': distance, 'delta_z': distance, 'lowest_before': state['lowest'],
            'lowest_after': lowest, 'lowest_expected_after': state['lowest'] + distance,
            'lowest_error': abs(lowest - (state['lowest'] + distance)),
            'lowest_tolerance': verified['lowest_tolerance'], 'mesh_proof': verified['mesh_proof'],
            'objects': sorted(obj.name for obj in state['objects']),
            'roots': [obj.name for obj in state['roots']],
            'meshes': [obj.name for obj in state['points']],
            'visible_meshes': sorted(obj.name for obj in state['visible']),
            'hidden_meshes': sorted(obj.name for obj in state['points'] if obj not in state['visible']),
            'hidden_mesh_policy': 'translation_invariant_local_modifiers_without_drivers',
            'vertices': sum(len(points) // 3 for points in state['points'].values()),
            'floor_widgets': floor_proof, 'placement_offsets': offset_proof}


def move(context, rig, distance):
    """Translate the whole recognized character by signed world Z distance."""
    distance = float(distance)
    if not math.isfinite(distance):
        raise ValueError('Enter a finite vertical distance.')
    return _translate(context, _prepare(context, rig), distance)


def _spline_shape_dependency(state, mesh):
    """Only a registered dress skin with an active owned Spline IK chain."""
    from . import skirt_rig
    record = skirt_rig.read_record(mesh)
    if record is None:
        return False
    rig = mesh.get(skirt_rig.RIG_KEY)
    modifier = mesh.modifiers.get(record['modifier'])
    if (rig not in state['rigs'] or modifier is None or modifier.type != 'ARMATURE'
            or not modifier.show_viewport or modifier.object != rig):
        return False
    for chain in record['chains']:
        curve = bpy.data.objects.get(chain['curve'])
        indices = {mesh.vertex_groups[name].index for name in chain['def'] if name in mesh.vertex_groups}
        if (curve not in state['objects'] or not indices
                or not any(group.group in indices and group.weight > 0.
                           for vertex in mesh.data.vertices for group in vertex.groups)):
            continue
        if any(con.type == 'SPLINE_IK' and not con.mute and con.influence > 0.
               and con.target == curve
               for name in chain['manual'] for con in rig.pose.bones[name].constraints):
            return True
    return False


def _nearby_floor_distances(state, distance):
    """Nearest float32 root-Z alternatives within a tighter floor precision.

    Native Hook/Spline IK world-matrix cancellation can amplify one float ULP.
    Only one unparented root with a world-vertical location input is supported;
    all other hierarchies retain the ordinary placement refusal. Every yielded
    alternative still needs every original shape/Rest/pose/world check.
    """
    if len(state['roots']) != 1:
        return
    root = state['roots'][0]
    if root.parent is not None:
        return
    delta = root.matrix_basis.to_3x3() @ state['world'][root].to_3x3().inverted() @ Vector((0., 0., distance))
    if abs(delta.x) > 1e-10 or abs(delta.y) > 1e-10 or not distance:
        return
    target = (Vector(state['channels'][root]['channels']['location']) + delta).z
    if not math.isfinite(target):
        return
    # Ordered float keys handle negative numbers and both signed zeros.
    bits = struct.unpack('!I', struct.pack('!f', target))[0]
    key = (~bits & 0xffffffff) if bits & 0x80000000 else bits | 0x80000000
    seen = {target}
    for step in range(1, _GROUND_NEIGHBOR_STEPS + 1):
        for signed_step in (step, -step):
            neighbor_key = key + signed_step
            if not 0 <= neighbor_key <= 0xffffffff:
                continue
            word = (neighbor_key & 0x7fffffff) if neighbor_key & 0x80000000 else (~neighbor_key & 0xffffffff)
            neighbor = struct.unpack('!f', struct.pack('!I', word))[0]
            adjustment = neighbor - target
            if not math.isfinite(neighbor) or neighbor in seen or abs(adjustment) > _GROUND_PRECISION:
                continue
            seen.add(neighbor)
            yield distance + adjustment, signed_step, adjustment


def ground(context, rig):
    """Place the character and its sole/Master outlines on world Z=0."""
    from . import floor_widgets, placement_offsets
    state = _prepare(context, rig)
    offset_state = placement_offsets.prepare(context, state['objects'], state['roots'])
    floor_state = floor_widgets.prepare(context, rig, align_planes=True)
    # An already grounded model must not oscillate between adjacent float32
    # positions on repeated clicks. This is tighter than the display/mesh bounds.
    distance = 0. if abs(state['lowest']) <= _GROUND_PRECISION else -state['lowest']
    try:
        return _translate(context, state, distance, floor_state=floor_state, offset_state=offset_state)
    except _ShapeChangeError as original:
        if not _spline_shape_dependency(state, original.mesh):
            raise
        for candidate, steps, adjustment in _nearby_floor_distances(state, distance):
            # Never replace the baseline after a failed attempt. First prove
            # the transaction's restored native outputs against that baseline.
            _verify(context, state, 0.)
            try:
                result = _translate(context, state, candidate, floor_state=floor_state,
                                    offset_state=offset_state, stable_floor=True)
            except (_ShapeChangeError, _FloorPrecisionError):
                continue
            result['stable_floor'] = {'requested_distance': distance,
                'adjustment': adjustment, 'planned_root_z_steps': steps,
                'accepted_root_z': float(state['roots'][0].location.z),
                'floor_precision': _GROUND_PRECISION,
                'original_shape_limits_retained': True}
            return result
        _verify(context, state, 0.)
        raise original


class CharacterDesignerGroundState(PropertyGroup):
    distance: FloatProperty(name='Vertical Distance', description='World vertical distance; positive moves up and negative moves down',
                            default=0., unit='LENGTH', subtype='DISTANCE')


class CHARACTERDESIGNER_OT_move_character(Operator):
    bl_idname = 'character_designer.move_character'
    bl_label = 'Move Character'
    bl_description = 'Move the whole recognized character by the signed world vertical distance, keeping its bones and local pose'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, 'object', None)
        return bool(obj and obj.type == 'ARMATURE' and context.mode in {'OBJECT', 'POSE'}
                    and obj.is_editable and not obj.library and not obj.override_library)

    def execute(self, context):
        from . import bone_display
        try:
            result = move(context, bone_display.character_rig(context), context.scene.character_designer_ground.distance)
            self.report({'INFO'}, f"Character moved {result['distance']:.6g} along world Z.")
            return {'FINISHED'}
        except _ERRORS as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_ground_character(Operator):
    bl_idname = 'character_designer.ground_character'
    bl_label = 'Move to Floor'
    bl_description = 'Automatically move the whole character so its lowest visible mesh point reaches world Z=0'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CHARACTERDESIGNER_OT_move_character.poll(context)

    def execute(self, context):
        from . import bone_display
        try:
            result = ground(context, bone_display.character_rig(context))
            self.report({'INFO'}, f"Character grounded; moved {result['distance']:.6g} along world Z.")
            return {'FINISHED'}
        except _ERRORS as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw(layout, context):
    settings = getattr(context.scene, 'character_designer_ground', None)
    if settings is None:
        return
    row = layout.row()
    row.enabled = CHARACTERDESIGNER_OT_ground_character.poll(context)
    row.operator('character_designer.ground_character', text='Move to Floor')


CHARACTER_GROUND_CLASSES = (
    CharacterDesignerGroundState,
    CHARACTERDESIGNER_OT_move_character,
    CHARACTERDESIGNER_OT_ground_character,
)
