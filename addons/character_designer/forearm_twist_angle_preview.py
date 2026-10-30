"""Reversible, post-animation forearm angle previews.

Only a session-owned Empty and final Hand rotation constraint are written.
Author pose inputs, animation bindings and curves are never changed. The
serialized receipt and native ID links deliberately make cleanup conservative:
an edited resource remains recoverable instead of being deleted by its name.
"""
import copy
import json
import math
import uuid

import bpy
from mathutils import Quaternion, Vector


SOURCE_REF = '_cd_forearm_angle_helper'
TOKEN = '_cd_forearm_angle_token'
SOURCE = '_cd_forearm_angle_source'
ARMATURE = '_cd_forearm_angle_armature'
MESH_DATA = '_cd_forearm_angle_mesh_data'
ARMATURE_DATA = '_cd_forearm_angle_armature_data'
SCHEMA = 1
TOLERANCE = 2.0e-5
MAX_ANGLE = math.radians(120.0)


class AnglePreviewError(ValueError):
    pass


def _fail(message):
    raise AnglePreviewError('Forearm angle preview: ' + message)


def _id_value(value):
    if isinstance(value, bpy.types.ID):
        return {'id_type': value.bl_rna.identifier, 'name': value.name_full,
                'library': value.library.filepath if value.library else None}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, 'to_list'):
        return [_id_value(item) for item in value.to_list()]
    if hasattr(value, 'items'):
        return {name: _id_value(item) for name, item in value.items()}
    try:
        return [_id_value(item) for item in value]
    except TypeError:
        _fail('an unsupported constraint property prevents a reversible preview.')


def _constraint_signature(constraint):
    values = {'type': constraint.type, 'name': constraint.name}
    for prop in constraint.bl_rna.properties:
        if prop.identifier == 'rna_type' or prop.is_readonly:
            continue
        if prop.type == 'COLLECTION':
            # Multi-target constraints contain writable nested structs. They
            # are outside this tiny preview's rollback contract.
            if len(getattr(constraint, prop.identifier)):
                _fail('a multi-target Hand constraint cannot be authenticated.')
            values[prop.identifier] = []
        elif prop.type == 'POINTER':
            value = getattr(constraint, prop.identifier)
            if value is not None and not isinstance(value, bpy.types.ID):
                _fail('a nested Hand constraint cannot be authenticated.')
            values[prop.identifier] = _id_value(value)
        else:
            values[prop.identifier] = _id_value(getattr(constraint, prop.identifier))
    try:
        custom_names = constraint.keys()
    except TypeError:
        # Native Blender constraints currently do not support IDProperties.
        custom_names = ()
    values['custom_properties'] = {name: _id_value(constraint[name]) for name in custom_names}
    return values


def _restore_active(constraints, signatures):
    # Adding/removing a constraint changes Blender's active-constraint UI flag.
    # Preserve that author selection as well as every solver property.
    for constraint in constraints:
        constraint.active = False
    for constraint, signature in zip(constraints, signatures):
        if signature.get('active'):
            constraint.active = True


def _author_pose(hand):
    return {name: _id_value(getattr(hand, name)) for name in
            ('location', 'scale', 'rotation_mode', 'rotation_euler',
             'rotation_quaternion', 'rotation_axis_angle')}


def _helper_signature(helper):
    return {name: _id_value(getattr(helper, name)) for name in
            ('name', 'type', 'location', 'rotation_mode', 'rotation_euler',
             'rotation_quaternion', 'rotation_axis_angle', 'scale',
             'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion',
             'delta_scale', 'hide_viewport', 'hide_render', 'hide_select',
             'empty_display_type', 'empty_display_size', 'show_in_front',
             'use_fake_user', 'parent', 'data')}


def _collection_signature(helper):
    return sorted((_id_value(collection) for collection in helper.users_collection),
                  key=lambda item: (item['id_type'], item['name']))


def _marker(runtime, mesh):
    try:
        value = json.loads(mesh[runtime.PREVIEW_KEY])
    except (KeyError, ValueError, TypeError) as exc:
        raise AnglePreviewError('Forearm angle preview: its recovery marker is missing or invalid.') from exc
    if not isinstance(value, dict):
        _fail('its recovery marker is invalid.')
    return value


def _publish(runtime, session, record):
    marker = _marker(runtime, session['mesh'])
    marker['angle_preview'] = record
    session['mesh'][runtime.PREVIEW_KEY] = json.dumps(marker)
    session['angle_preview'] = copy.deepcopy(record)


def _record(runtime, session):
    marker = _marker(runtime, session['mesh'])
    record = session.get('angle_preview')
    if record != marker.get('angle_preview'):
        _fail('the live receipt and recovery marker disagree; keep them for recovery.')
    if record is not None and (not isinstance(record, dict) or record.get('schema') != SCHEMA):
        _fail('its recovery receipt has an unsupported format.')
    return record


def _id_references(value, target):
    if isinstance(value, bpy.types.ID):
        return int(value == target)
    if hasattr(value, 'values'):
        return sum(_id_references(item, target) for item in value.values())
    if hasattr(value, 'to_list'):
        return sum(_id_references(item, target) for item in value.to_list())
    return 0


def _external_references(helper, mesh, arm, owned, expected_users):
    if helper.users != expected_users:
        _fail('the temporary helper has another native user; remove that reference before retrying.')
    # user_map catches references in node trees, other datablocks and linked
    # collections. Counted users plus explicit same-ID checks also distinguish
    # another reference from the already-authorized mesh/armature ID.
    master_scenes = [scene for scene in bpy.data.scenes if scene.collection in helper.users_collection]
    allowed = {mesh, arm, *helper.users_collection, *master_scenes}
    users = bpy.data.user_map(subset={helper}).get(helper, set())
    if set(users) - allowed:
        _fail('another datablock references the temporary helper.')
    for owner in (mesh, arm, *helper.users_collection, *master_scenes):
        count = sum(_id_references(owner[name], helper) for name in owner.keys())
        if count != int(owner == mesh):
            _fail('another custom property references the temporary helper.')
    for obj in bpy.data.objects:
        if obj.parent == helper:
            _fail('another object is parented to the temporary helper.')
        constraints = list(obj.constraints)
        if obj.pose:
            constraints += [con for bone in obj.pose.bones for con in bone.constraints]
        for con in constraints:
            if con == owned:
                continue
            for prop in con.bl_rna.properties:
                if prop.type == 'POINTER' and getattr(con, prop.identifier, None) == helper:
                    _fail('another constraint references the temporary helper.')
        animation = obj.animation_data
        for curve in getattr(animation, 'drivers', ()):
            for variable in curve.driver.variables:
                if any(target.id == helper for target in variable.targets):
                    _fail('another driver references the temporary helper.')


def _animation_paths(arm):
    animation = arm.animation_data
    actions = [animation.action] if animation and animation.action else []
    if animation:
        actions += [strip.action for track in animation.nla_tracks for strip in track.strips
                    if strip.action is not None]
    paths = {curve.data_path for curve in getattr(animation, 'drivers', ())}
    for action in set(actions):
        if hasattr(action, 'fcurves'):
            paths.update(curve.data_path for curve in action.fcurves)
        else:
            paths.update(curve.data_path for layer in action.layers for strip in layer.strips
                         for bag in getattr(strip, 'channelbags', ()) for curve in bag.fcurves)
    return paths


def _original_constraints_match(hand, constraints, signatures, arm, *, allow_animation=False):
    if len(constraints) != len(signatures):
        return False
    paths = _animation_paths(arm) if allow_animation else set()
    for constraint, saved in zip(constraints, signatures):
        current = _constraint_signature(constraint)
        if set(current) != set(saved):
            return False
        for name, old in saved.items():
            if current[name] == old:
                continue
            prop = constraint.bl_rna.properties.get(name)
            if (name in {'name', 'type', 'custom_properties'} or prop is None or
                    not prop.is_animatable or constraint.path_from_id(name) not in paths):
                return False
    return True


def _exclusive_display_owner(arm):
    """Changing Rest/Pose display affects every object using Armature data."""
    if (arm.library or arm.override_library or arm.data.library or arm.data.override_library or
            not getattr(arm, 'is_editable', True) or not getattr(arm.data, 'is_editable', True)):
        _fail('Rest Position needs a local editable Armature before testing an angle.')
    owners = [obj for obj in bpy.data.objects if obj.type == 'ARMATURE' and obj.data == arm.data]
    if owners != [arm]:
        _fail('Rest Position uses shared Armature data; make that data single-user before testing an angle.')


def _restore_display(arm, record):
    if record.get('pose_position_changed', False):
        _exclusive_display_owner(arm)
        arm.data.pose_position = record['original_pose_position']


def _original(session, record, *, releasing=False):
    mesh, arm = session['mesh'], session.get('armature')
    if arm is None or mesh.name != record['source_name'] or arm.name != record['armature_name']:
        _fail('the original Mesh or Armature was removed or renamed; preserve the recovery marker.')
    if (session.get('target') != record['target_name'] or
            any(name not in arm.pose.bones for name in record['chain']) or
            record['target_name'] not in arm.pose.bones):
        _fail('an original target bone was removed or renamed; preserve the recovery marker.')
    hand = arm.pose.bones[record['hand_name']]
    if not releasing and _author_pose(hand) != record['author_pose']:
        _fail('the Hand author inputs changed; preserve those edits and the recovery marker.')
    if [list(value for row in arm.data.bones[name].matrix_local for value in row)
            for name in record['chain']] != record['rest']:
        _fail('the original arm Rest changed; preserve the recovery marker.')
    if _id_value(mesh.data) != record['mesh_data'] or _id_value(arm.data) != record['armature_data']:
        _fail('the original Mesh or Armature data changed; preserve the recovery marker.')
    if record.get('pose_position_changed', False):
        _exclusive_display_owner(arm)
        if (record.get('original_pose_position') != 'REST' or
                (not releasing and arm.data.pose_position != 'POSE')):
            _fail('the temporary Pose display changed; preserve the recovery marker.')
    return mesh, arm, hand


def _authenticate(runtime, session, record, *, releasing=False):
    mesh, arm, hand = _original(session, record, releasing=releasing)
    helper = mesh.get(SOURCE_REF)
    if not isinstance(helper, bpy.types.Object):
        _fail('the temporary helper native link is missing; preserve the recovery marker.')
    if (helper.get(SOURCE) != mesh or helper.get(ARMATURE) != arm or
            helper.get(MESH_DATA) != mesh.data or helper.get(ARMATURE_DATA) != arm.data or
            helper.get(TOKEN) != record['token']):
        _fail('the temporary helper ownership links changed; preserve the recovery marker.')
    if set(helper.keys()) != {SOURCE, ARMATURE, MESH_DATA, ARMATURE_DATA, TOKEN}:
        _fail('the temporary helper custom properties changed; preserve the recovery marker.')
    if (helper.parent is not None or helper.animation_data is not None or helper.constraints or
            helper.modifiers or helper.children or _helper_signature(helper) != record['helper_signature'] or
            _collection_signature(helper) != record['collections']):
        _fail('the temporary helper was edited; preserve the recovery marker.')
    constraints = list(hand.constraints)
    owned = constraints[-1] if constraints and constraints[-1].name == record['constraint_name'] else None
    if owned is not None:
        if (owned.target != helper or _constraint_signature(owned) != record['constraint_signature'] or
                not _original_constraints_match(hand, constraints[:-1], record['original_constraints'], arm,
                                                allow_animation=releasing)):
            _fail('the temporary rotation constraint or original constraint order changed.')
    elif not (releasing and record.get('phase') == 'releasing' and
              _original_constraints_match(hand, constraints, record['original_constraints'], arm,
                                          allow_animation=True)):
        _fail('the temporary rotation constraint was removed, renamed or reordered.')
    _external_references(helper, mesh, arm, owned, record['helper_users'] - int(owned is None))
    return helper, hand, owned


def _create(context, runtime, session):
    mesh, arm = session['mesh'], session['armature']
    if SOURCE_REF in mesh:
        _fail('an existing temporary helper link must be recovered first.')
    chain = list(session['chain'])
    lower_name, hand_name = chain[1:]
    hand = arm.pose.bones[hand_name]
    original_pose_position = arm.data.pose_position
    if original_pose_position == 'REST':
        _fail('enter the editable Rest Pose before testing an angle; native Rest Position cannot be posed.')
    # New previews never expose the underlying animation by toggling display.
    # The old receipt fields remain supported for existing126 recovery only.
    changed_pose_position = False
    try:
        context.view_layer.update()
        original_constraints = [_constraint_signature(con) for con in hand.constraints]
        evaluated = arm.evaluated_get(context.evaluated_depsgraph_get())
        lower_rest, hand_rest = (arm.data.bones[name].matrix_local for name in (lower_name, hand_name))
        lower_deform = evaluated.pose.bones[lower_name].matrix @ lower_rest.inverted()
        hand_deform = evaluated.pose.bones[hand_name].matrix @ hand_rest.inverted()
        axis = (arm.data.bones[lower_name].tail_local - arm.data.bones[lower_name].head_local).normalized()
        lower_rotation = lower_deform.to_quaternion().normalized()
        relative = lower_rotation.conjugated() @ hand_deform.to_quaternion().normalized()
        initial_angle = runtime.twist_angle(lower_deform, hand_deform, axis)
        swing = (relative @ Quaternion(axis, -initial_angle)).normalized()
    except Exception:
        if changed_pose_position and arm.data.pose_position != original_pose_position:
            arm.data.pose_position = original_pose_position
        raise
    token = uuid.uuid4().hex
    helper = owned = None
    try:
        helper = bpy.data.objects.new('Forearm Angle Preview', None)
        helper.rotation_mode = 'QUATERNION'
        helper.rotation_quaternion = evaluated.pose.bones[hand_name].matrix.to_quaternion().normalized()
        helper.hide_viewport = helper.hide_render = helper.hide_select = True
        helper.empty_display_type = 'PLAIN_AXES'
        helper.empty_display_size = 0.001
        helper[SOURCE], helper[ARMATURE] = mesh, arm
        helper[MESH_DATA], helper[ARMATURE_DATA] = mesh.data, arm.data
        helper[TOKEN] = token
        context.scene.collection.objects.link(helper)
        mesh[SOURCE_REF] = helper
        owned = hand.constraints.new('COPY_ROTATION')
        owned.name = 'Forearm Angle Preview ' + token[:12]
        owned.target = helper
        owned.owner_space, owned.target_space = 'POSE', 'WORLD'
        owned.mix_mode = 'REPLACE'
        owned.use_offset = False
        owned.use_x = owned.use_y = owned.use_z = True
        owned.active = False
        _restore_active(list(hand.constraints)[:-1], original_constraints)
        record = {'schema': SCHEMA, 'token': token, 'phase': 'active',
                  'source_name': mesh.name, 'armature_name': arm.name,
                  'original_pose_position': original_pose_position,
                  'pose_position_changed': changed_pose_position,
                  'mesh_data': _id_value(mesh.data), 'armature_data': _id_value(arm.data),
                  'target_name': session['target'], 'hand_name': hand_name, 'chain': chain,
                  'rest': [list(value for row in arm.data.bones[name].matrix_local for value in row)
                           for name in chain], 'author_pose': _author_pose(hand),
                  'original_constraints': original_constraints, 'constraint_name': owned.name,
                  'constraint_signature': _constraint_signature(owned),
                  'helper_signature': _helper_signature(helper),
                  'collections': _collection_signature(helper), 'helper_users': helper.users,
                  'lower_rotation': list(lower_rotation), 'swing': list(swing),
                  'axis': list(axis), 'rest_hand_rotation': list(hand_rest.to_quaternion()),
                  'last_angle': initial_angle, 'desired': list(helper.rotation_quaternion)}
        _publish(runtime, session, record)
        return record
    except Exception:
        if owned is not None:
            hand.constraints.remove(owned)
            _restore_active(list(hand.constraints), original_constraints)
        if changed_pose_position:
            arm.data.pose_position = original_pose_position
        if mesh.get(SOURCE_REF) == helper:
            del mesh[SOURCE_REF]
        if helper is not None:
            bpy.data.objects.remove(helper, do_unlink=True)
        raise


def _rotation_error(actual, wanted):
    residual = (actual.normalized().conjugated() @ wanted.normalized()).normalized()
    return 2.0 * math.atan2(Vector((residual.x, residual.y, residual.z)).length, abs(residual.w))


def _check_reached(context, runtime, session, record, wanted):
    evaluated = session['armature'].evaluated_get(context.evaluated_depsgraph_get())
    actual_rotation = evaluated.pose.bones[record['hand_name']].matrix.to_quaternion()
    actual_angle = runtime.current_twist_angle(context)
    twist_error = abs(math.atan2(math.sin(actual_angle - record['last_angle']),
                                 math.cos(actual_angle - record['last_angle'])))
    rotation_error = _rotation_error(actual_rotation, wanted)
    if max(twist_error, rotation_error) > TOLERANCE:
        _fail(f'the requested angle was not reached (twist error {twist_error:.6g}, '
              f'rotation error {rotation_error:.6g} radians); restore the preview before retrying.')
    return {'angle': actual_angle, 'twist_error': twist_error, 'rotation_error': rotation_error}


def set_angle(context, runtime, session, angle):
    """Apply and verify an absolute trial twist, retaining the entry wrist swing.

    On a refusal the receipt stays available. The caller must run its ordinary
    session rollback, which calls ``release`` before clearing that receipt.
    """
    previous_busy = runtime._BUSY
    runtime._BUSY = True
    try:
        result = _set_angle(context, runtime, session, angle)
    finally:
        runtime._BUSY = previous_busy
    runtime.update_runtime(context.scene, context.evaluated_depsgraph_get())
    return result


def _set_angle(context, runtime, session, angle):
    if not math.isfinite(angle) or abs(angle) > MAX_ANGLE + 1.e-7:
        _fail('the test angle must be finite and within ±120 degrees.')
    record = _record(runtime, session)
    if record is None:
        record = _create(context, runtime, session)
    helper, _hand, _owned = _authenticate(runtime, session, record)
    if record.get('phase') != 'active':
        _fail('cleanup is incomplete; finish recovery before changing the angle.')
    wanted = (Quaternion(record['lower_rotation']) @ Quaternion(record['swing']) @
              Quaternion(Vector(record['axis']), angle) @ Quaternion(record['rest_hand_rotation'])).normalized()
    previous_record = copy.deepcopy(record)
    previous_rotation = helper.rotation_quaternion.copy()
    try:
        helper.rotation_quaternion = wanted
        record['helper_signature'] = _helper_signature(helper)
        record['last_angle'], record['desired'] = float(angle), list(wanted)
        _publish(runtime, session, record)
        helper.update_tag(refresh={'OBJECT'})
        session['armature'].update_tag(refresh={'OBJECT'})
        context.view_layer.update()
        return _check_reached(context, runtime, session, record, wanted)
    except Exception:
        helper.rotation_quaternion = previous_rotation
        _publish(runtime, session, previous_record)
        helper.update_tag(refresh={'OBJECT'})
        session['armature'].update_tag(refresh={'OBJECT'})
        context.view_layer.update()
        raise


def _clear_receipt(context, runtime, session, record):
    marker = _marker(runtime, session['mesh'])
    if marker.get('angle_preview') != record:
        _fail('the recovery marker changed during cleanup.')
    if session['mesh'].get(SOURCE_REF) is not None:
        _fail('the helper native link did not clear after removal.')
    if SOURCE_REF in session['mesh']:
        del session['mesh'][SOURCE_REF]
    marker.pop('angle_preview')
    session['mesh'][runtime.PREVIEW_KEY] = json.dumps(marker)
    session.pop('angle_preview', None)


def owned_mesh_data_reference(runtime, mesh):
    """Prove the one extra Mesh-data user is this live preview's native link.

    The ordinary single-user guard may admit exactly this owner after verifying
    the complete receipt. A second real Mesh Object or any other native user
    still refuses. No name-only or global marker scan earns this exception.
    """
    session = getattr(runtime, '_SESSION', None)
    if session is None or session.get('mesh') != mesh:
        return False
    try:
        if mesh.data.users != 2 or mesh.data.use_fake_user or getattr(mesh.data, 'use_extra_user', False):
            return False
        record = _record(runtime, session)
        if record is None:
            return False
        helper, _hand, _owned = _authenticate(runtime, session, record, releasing=True)
        if helper.get(MESH_DATA) != mesh.data:
            return False
        return set(bpy.data.user_map(subset={mesh.data}).get(mesh.data, set())) == {mesh, helper}
    except (AnglePreviewError, KeyError, ValueError, TypeError, AttributeError, ReferenceError, RuntimeError):
        # This is a proof predicate used by an existing refusal, never cleanup.
        # A failed proof leaves the ordinary single-user error and all resources.
        return False


def release(context, runtime, session):
    """Authenticate every resource before removing this session's own override."""
    if 'angle_preview' not in session:
        try:
            session['mesh'].name
        except ReferenceError:
            # This ordinary session never published angle resources. Preserve
            # the runtime's pre-existing dangling-Mesh rollback behavior.
            return False
    if 'angle_preview' not in session and SOURCE_REF not in session['mesh']:
        raw = session['mesh'].get(runtime.PREVIEW_KEY)
        if raw is None:
            return False
        marker = _marker(runtime, session['mesh'])
        if 'angle_preview' not in marker:
            return False
    record = _record(runtime, session)
    if record is None:
        if SOURCE_REF in session['mesh']:
            _fail('a helper link exists without its recovery receipt.')
        return False
    if record.get('phase') == 'releasing' and session['mesh'].get(SOURCE_REF) is None:
        _mesh, _arm, hand = _original(session, record, releasing=True)
        if (not _original_constraints_match(hand, list(hand.constraints), record['original_constraints'], _arm,
                                            allow_animation=True) or
                any(obj.get(TOKEN) == record['token'] for obj in bpy.data.objects)):
            _fail('interrupted cleanup still has an unlinked resource; retain the recovery marker.')
        _restore_display(_arm, record)
        _clear_receipt(context, runtime, session, record)
        return True
    helper, hand, owned = _authenticate(runtime, session, record, releasing=True)
    # Record the destructive phase first. An interrupted cleanup may retry only
    # the exact original constraint list, never silently bless a deleted owner.
    record['phase'] = 'releasing'
    _publish(runtime, session, record)
    if owned is not None:
        hand.constraints.remove(owned)
        _restore_active(list(hand.constraints), record['original_constraints'])
    _restore_display(session['armature'], record)
    bpy.data.objects.remove(helper, do_unlink=True)
    _clear_receipt(context, runtime, session, record)
    return True
