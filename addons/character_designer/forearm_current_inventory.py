"""Current-native inventory for existing paired Forearm corrective outputs.

The existing calibration owns only its Mesh's corrective Shape Keys. Its current
input validation does not borrow historical Main Rig restoration authority.
Each relevant ARM graph is checked by the existing strict local wrist reader,
against actual native Rest data. Unrelated body/display inventories are outside
the corrective output's input domain.
This reader creates no calibration, Rest exception, persistent capability or
recovery metadata. Main Rig restoration, switching and export keep their own
complete validators. Original retains its authenticated mute/Rest route.
"""


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _local_binding(obj, armature, scene, bpy, forearm):
    _require(isinstance(armature, bpy.types.Object) and armature.type == 'ARMATURE'
             and isinstance(armature.data, bpy.types.Armature)
             and isinstance(scene, bpy.types.Scene)
             and not armature.library and not armature.override_library
             and not armature.data.library and not armature.data.override_library
             and armature.is_editable and armature.data.is_editable,
             'Forearm current-native validation needs the original local editable Main Rig.')
    _require(scene.objects.get(armature.name) is armature
             and isinstance(obj, bpy.types.Object) and obj.type == 'MESH'
             and scene.objects.get(obj.name) is obj
             and not obj.library and not obj.override_library
             and not obj.data.library and not obj.data.override_library
             and obj.is_editable and obj.data.is_editable,
             'Keep the calibrated body and Main Rig in their current saved Scene.')
    bound = [candidate for candidate in bpy.data.objects
             if candidate.type == 'ARMATURE' and candidate.data is armature.data]
    _require(len(bound) == 1 and bound[0] is armature,
             'Another Object actually shares the Forearm Armature data; preserve both bindings.')
    _require(forearm._check_mesh(obj) is armature,
             'The calibrated Forearm body is bound to another Main Rig.')


def _calibrations(obj, armature, records, forearm, inventory=None):
    _require(isinstance(records, dict) and set(records) == {'L', 'R'},
             'Forearm current-native validation requires both existing arm calibrations.')
    for side in ('L', 'R'):
        record = records[side]
        _require(isinstance(record, dict), 'The existing Forearm calibration is invalid.')
        chain = record.get('chain')
        _require(isinstance(chain, list) and len(chain) == 3
                 and all(isinstance(name, str) and name for name in chain)
                 and len(set(chain)) == 3
                 and all(armature.data.bones.get(name) is not None for name in chain)
                 and record.get('armature') == armature.name,
                 'The existing Forearm calibration has another rig or arm chain.')
        _require(record.get('rest') == forearm._rest_signature(armature, chain),
                 'The existing Forearm arm-chain Rest changed; its calibration was kept.')
        _require(type(record.get('enabled', True)) is bool,
                 'The existing Forearm enabled state is invalid.')
        if inventory is not None:
            route = inventory['rigs'].get(('ARM', side))
            _require(route is not None and list(route['chain']) == chain,
                     'The current owned arm chain differs from its existing Forearm calibration.')


def _targets(armature, records, limb):
    """Resolve owned native Hand Targets; saved target aliases may be FK hands."""
    targets = {}
    armature_id = armature.data.get(limb.ARMATURE_ID_KEY)
    for side in ('L', 'R'):
        candidates = [bone for bone in armature.data.bones
                      if bone.get(limb.OWNER_KEY) == limb.OWNER_VALUE
                      and bone.get(limb.ROLE_KEY) == 'HAND_IK'
                      and bone.get(limb.KIND_KEY) == 'ARM'
                      and bone.get(limb.SIDE_KEY) == side]
        _require(len(candidates) == 1,
                 'The calibrated Forearm has no unique owned Hand Target for ' + side + '.')
        bone = candidates[0]
        _require(limb._owned(bone, armature_id, role='HAND_IK',
                            rig_id=bone.get(limb.RIG_ID_KEY))
                 and limb.validation_json.decode(bone.get(limb.CHAIN_KEY, '')) == records[side]['chain'],
                 'The owned Hand Target does not belong to the existing Forearm arm chain.')
        target = armature.pose.bones.get(bone.name)
        _require(target is not None and target.bone == bone,
                 'The owned Hand Target has no matching native Pose Bone.')
        targets[side] = target
    return targets


def _pointer(value):
    return value.as_pointer() if hasattr(value, 'as_pointer') else id(value)


def _local_constraints(armature, side, route, limb):
    """Reject extra native relations on the exact local deformation graph."""
    names = set(route['chain'])
    for field in ('target', 'pole', 'line', 'display', 'solver_target', 'heel',
                  'mch_upper', 'mch_lower', 'ori_upper', 'ori_lower'):
        bone = route.get(field)
        if bone is not None:
            names.add(bone.name)
    if route.get('auto_rotation_space') == 'PARENT_DELTA':
        names.add(limb._wrist_helper_name(side))
    allowed = {(_pointer(pose), _pointer(constraint))
               for pose, constraint, _record in route['entries']}
    for name in names:
        pose = armature.pose.bones.get(name)
        _require(pose is not None and pose.id_data is armature,
                 'A calibrated Forearm local graph bone is missing or foreign.')
        _require(all((_pointer(pose), _pointer(constraint)) in allowed
                     for constraint in pose.constraints),
                 'An unverified constraint was added to the calibrated Forearm local graph: ' + name)


def validate_current_native(obj, armature, *, scene=None, sides=None):
    """Validate existing paired corrections, without historical Main authority.

    ``sides`` does not narrow the paired protection: both saved arm chains must
    still match. The caller retains the normal topology, support, key ownership
    and numeric checks after this read-only inventory preparation. No capture,
    recapture, metadata migration or output write belongs to this function.
    Empty/single-side or bare native-FK calibrations and Original return None
    for the existing route; changed paired generated calibrations and native
    validation failures refuse.
    Existing enabled/disabled choices remain the numeric writer's policy.
    """
    import bpy
    from . import body_original_mode as original
    from . import forearm_twist as forearm, limb_ik, limb_ik_fk
    from . import limb_ik_wrist_rotation as wrist, validation_json, validation_native

    if original.active(armature):
        return None
    records = forearm._records(obj)
    if not records or set(records) != {'L', 'R'}:
        return None
    # Paired native-FK captures also exist without generated controls. They
    # retain their original resolver. Partial/damaged generated ownership must
    # still reach the strict local checks instead of borrowing that old route.
    if not any(bone.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
               and bone.get(limb_ik.ROLE_KEY) == 'HAND_IK'
               for bone in armature.data.bones):
        return None
    required = set(('L', 'R') if sides is None else sides)
    _require(required and required <= {'L', 'R'}, 'Choose an explicit Forearm arm scope.')
    scene = bpy.context.scene if scene is None else scene
    _local_binding(obj, armature, scene, bpy, forearm)
    data = armature.data
    _calibrations(obj, armature, records, forearm)
    with validation_json.read_scope(), validation_native.read_scope():
        targets = _targets(armature, records, limb_ik)
        routes = {}
        for side in ('L', 'R'):
            key, route = wrist._local_inventory(limb_ik, limb_ik_fk, armature, targets[side])
            _require(key == ('ARM', side), 'The local Forearm validator returned another arm side.')
            _local_constraints(armature, side, route, limb_ik)
            routes[key] = route
    _require(armature.data is data and not original.active(armature),
             'The Forearm native data or Original mode changed during validation.')
    _local_binding(obj, armature, scene, bpy, forearm)
    _require(forearm._records(obj) == records,
             'The existing Forearm calibration changed during native validation.')
    # This deliberately small DTO is for _forearm_inventory only. It is not a
    # generic whole-body inventory, saved Rest view or restoration capability.
    inventory = {'scope': 'FOREARM_CURRENT_NATIVE',
                 'armature_id': data.get(limb_ik.ARMATURE_ID_KEY),
                 'schema': data.get(limb_ik.SCHEMA_KEY, limb_ik.LEGACY_SCHEMA),
                 'target_rotation_version': data.get(limb_ik.TARGET_ROTATION_VERSION_KEY),
                 'rigs': routes}
    _calibrations(obj, armature, records, forearm, inventory)
    return inventory
