"""Staged M1 FK scope proof. Imports alone do not require Blender or mutate IDs.

The complete packet/native mapping and all installed ownership validators run
before narrowing the editable upperbody inputs. A generated name is never an
authorization, and the source Rest adapter is the existing authenticated Main
Rig transaction view. IK, fractional switches and partial coupled chains are
deliberately unsupported in this first direct-edit policy.
"""
from dataclasses import dataclass
import hashlib
import importlib
import math
import struct

try:
    from . import animation_composition_contract as contract
except ImportError:
    import animation_composition_contract as contract

POLICY = 'cdesigner.composition-effective-fk/1'
MAX_BONES = 1024
TRANSFORMS = ('location', 'rotation_euler', 'rotation_quaternion',
              'rotation_axis_angle', 'scale')


def _host(name):
    return importlib.import_module((__package__ or 'character_designer') + '.' + name)


def _require(value, message):
    if not value:
        raise ValueError(message)


def _ordinal(text):
    # StringComparer.Ordinal compares UTF-16 code units, including astral text.
    return text.encode('utf-16-be', errors='strict')


def source_rest_sha256(packet):
    """Frozen C# rest identity bytes, from the re-read published JSON values.

    Packet order supplies parent indices. Sorting the output must not rewrite
    them. No Blender Rest serialization or source pre-JSON floats substitute.
    """
    bones = packet.get('bones') if isinstance(packet, dict) else None
    _require(isinstance(bones, list) and 0 < len(bones) <= MAX_BONES,
             'Composition needs a complete bounded B packet bone inventory.')
    rows, paths = [], set()
    for index, bone in enumerate(bones):
        _require(isinstance(bone, dict), 'Invalid B packet Rest identity.')
        values = []
        for field in ('path', 'humanRole', 'restSource'):
            value = bone.get(field)
            _require(isinstance(value, str) and len(value) <= 8192,
                     'Invalid B packet Rest ' + field + '.')
            raw = value.encode('utf-8', errors='strict')
            values.append(struct.pack('<I', len(raw)) + raw)
        path, parent, rest = bone['path'], bone.get('parent'), bone.get('rest')
        _require(path and path not in paths, 'B packet contains duplicate or empty paths.')
        paths.add(path)
        # Identity serialization keeps the published int32 verbatim. The
        # packet loader / strict mapping separately validates its hierarchy.
        _require(type(parent) is int and -(2**31) <= parent < 2**31,
                 'Invalid B packet parent identity.')
        _require(isinstance(rest, list) and len(rest) == 16 and
                 all(type(v) in (int, float) and math.isfinite(v) for v in rest),
                 'Invalid B packet Rest matrix.')
        try:
            matrix = struct.pack('<16f', *rest)
        except (OverflowError, struct.error) as error:
            raise ValueError('B packet Rest exceeds finite float32.') from error
        _require(all(math.isfinite(v) for v in struct.unpack('<16f', matrix)),
                 'B packet Rest exceeds finite float32.')
        rows.append((path, b''.join(values) + struct.pack('<i', parent) + matrix))
    digest = hashlib.sha256()
    for _path, raw in sorted(rows, key=lambda row: _ordinal(row[0])):
        digest.update(raw)
    return digest.hexdigest()


def resolve_packet_paths(spec, packet, mapping_indices):
    """Pure narrowing of an already authenticated full native mapping.

    Returns native -> exact packet row. Neither names nor suffixes select a
    role. A path that maps to a different explicit human role fails closed.
    """
    _require(source_rest_sha256(packet) == spec['sourceRestSha256'],
             'Composition source Rest differs from the published B packet.')
    bones = packet['bones']
    by_path = {bone['path']: (index, bone) for index, bone in enumerate(bones)}
    inverse = {}
    for name, index in mapping_indices.items():
        _require(isinstance(name, str) and type(index) is int and 0 <= index < len(bones)
                 and index not in inverse and bones[index].get('name') == name,
                 'Composition requires the exact authenticated native mapping.')
        inverse[index] = name
    result, roles = {}, set()
    for path in spec['allowedBonePaths']:
        _require(path in by_path, 'Composition path is absent from the exact B packet: ' + path)
        index, bone = by_path[path]
        role = bone['humanRole']
        _require(index in inverse and role in contract.ALLOWED_ROLES and role in spec['humanRoles'],
                 'Composition path has no authenticated allowed native role: ' + path)
        _require(role not in roles, 'Composition has an ambiguous repeated human role: ' + role)
        roles.add(role)
        result[inverse[index]] = bone
    _require(roles == set(spec['humanRoles']),
             'Composition humanRoles and allowedBonePaths identify different scopes.')
    return result


def require_complete_groups(names, groups):
    names = set(names)
    for label, values in groups:
        group = set(values)
        _require(not group & names or group <= names,
                 'Composition partially owns coupled ' + label + '; select its complete FK chain.')


def _mapping_for_scope(rig, packet, unit_scale):
    """Re-run the importer's strict graph/bind proof during read-only Tweak.

    The only omitted check is the operational prohibition on *starting an
    import* in NLA Tweak. This function does not start an import or write a
    pose. Matrix, local-ID, inheritance, driver, parent, unit, full Dress and
    bind-position checks retain the frozen .24 import policy and tolerances.
    """
    ua, controls = _host('unity_animation'), _host('unity_animation_controls')
    if not (rig.animation_data and rig.animation_data.use_tweak_mode):
        return ua._mapping(rig, packet, unit_scale)
    import numpy as np
    from mathutils import Matrix
    candidates = {}
    for index, bone in enumerate(packet['bones']):
        if bone['name'] in rig.data.bones:
            _require(bone['name'] not in candidates, 'Ambiguous exported native bone.')
            candidates[bone['name']] = index
    _require('Hips' in candidates and len(candidates) >= 4,
             'This package does not contain this character\'s named body bones.')
    plan = controls.build_plan(rig, candidates, package=packet)
    _require(rig.type == 'ARMATURE' and rig.mode != 'EDIT' and not rig.library and
             not rig.data.library and rig.is_editable and not rig.parent and
             not rig.constraints and rig.data.pose_position == 'POSE',
             'Use a local unparented native Armature in Pose Position.')
    axes = [rig.matrix_world.to_3x3().col[i].copy() for i in range(3)]
    lengths = [axis.length for axis in axes]
    _require(min(lengths) >= 1e-8 and max(lengths) - min(lengths) <= max(lengths) * 1e-5
             and rig.matrix_world.to_3x3().determinant() > 0 and
             all(abs(axes[i].dot(axes[j])) <= lengths[i] * lengths[j] * 1e-5
                 for i, j in ((0, 1), (0, 2), (1, 2))),
             'The character needs positive uniform object scale without shear.')
    for name, index in candidates.items():
        pb = rig.pose.bones[name]
        _require(all(plan and (name, c.name) in plan.constraint_keys for c in pb.constraints)
                 and pb.bone.use_inherit_rotation and pb.bone.inherit_scale == 'FULL',
                 'Native constraints or transform inheritance are not supported: ' + name)
        parent = packet['bones'][index]['parent']
        while parent >= 0 and packet['bones'][parent]['name'] not in candidates:
            parent = packet['bones'][parent]['parent']
        exported_parent = packet['bones'][parent]['name'] if parent >= 0 else None
        actual = pb.bone.parent
        while actual and plan and actual.name in plan.control_names:
            actual = actual.parent
        _require(exported_parent == (actual.name if actual else None),
                 'Different exported native parent: ' + name)
    ad = rig.animation_data
    prefixes = tuple(rig.pose.bones[name].path_from_id() for name in candidates)
    _require(not ad or all(plan and (f.data_path, f.array_index) in plan.driver_keys
                          or not (f.data_path.startswith(prefixes) or
                                  not f.data_path.startswith('pose.bones['))
                          for f in ad.drivers), 'Unrecognized native or object driver.')
    _require(not rig.data.animation_data, 'Animated Armature data is unsupported.')
    rests = {name: rig.matrix_world @ rig.data.bones[name].matrix_local for name in candidates}
    bound = [(name, index) for name, index in candidates.items()
             if packet['bones'][index]['restSource'] == 'bindpose']
    _require(len(bound) >= 4, 'Too few verified skin bind poses.')
    source = np.asarray([tuple(ua._matrix(packet['bones'][i]['rest'], name).translation)
                         for name, i in bound], dtype=np.float64)
    dest = np.asarray([tuple(rests[name].translation) for name, _i in bound], dtype=np.float64)
    sc, dc = source.mean(axis=0), dest.mean(axis=0)
    x, y = source - sc, dest - dc
    _require(np.linalg.matrix_rank(x, tol=1e-6) >= 3 and
             np.linalg.matrix_rank(y, tol=1e-6) >= 3, 'Bind skeleton cannot prove its 3D mapping.')
    u, singular, vt = np.linalg.svd(x.T @ y)
    rotation = u @ vt
    scale = float(singular.sum() / (x * x).sum())
    _require(math.isfinite(scale) and abs(scale * unit_scale - 1.) <= .002,
             'Unity and Blender character sizes differ; re-export the same character first.')
    translation = dc - sc @ rotation * scale
    affine = np.eye(4)
    affine[:3, :3], affine[:3, 3] = rotation.T * scale, translation
    conversion = Matrix(affine.tolist())
    errors = np.linalg.norm(source @ rotation * scale + translation - dest, axis=1) * unit_scale
    error = float(np.max(errors))
    _require(error <= .0002, 'Bind skeletons differ from the exported character.')
    for name, index in candidates.items():
        delta = conversion @ ua._matrix(packet['bones'][index]['rest'], name).translation - rests[name].translation
        _require(delta.length * unit_scale <= .0002, 'Different original native bind position: ' + name)
    return ua.Mapping(candidates, conversion, error, rests, plan)


def _rotation(mode):
    return ('rotation_quaternion', 4) if mode == 'QUATERNION' else (
        ('rotation_axis_angle', 4) if mode == 'AXIS_ANGLE' else ('rotation_euler', 3))


def _defaults(pb):
    """Canonical unkeyed pose inputs, independent of current evaluated A/C."""
    rotation, count = _rotation(pb.rotation_mode)
    values = {'location': (0., 0., 0.), 'scale': (1., 1., 1.),
              'rotation_quaternion': (1., 0., 0., 0.),
              'rotation_euler': (0., 0., 0.), 'rotation_axis_angle': (0., 0., 1., 0.)}
    return {(pb.path_from_id(prop), i): float(value)
            for prop in ('location', rotation, 'scale')
            for i, value in enumerate(values[prop][:count if prop == rotation else 3])}


@dataclass(frozen=True)
class Scope:
    channels: tuple
    defaults: dict
    native_names: tuple
    path_by_native: dict
    input_by_native: dict
    control_ids: tuple
    canonical_defaults: dict
    fixed_properties: tuple  # (pose-bone, property, value)
    admission_properties: tuple  # Only mode inputs coupled to selected B sources.
    auxiliary_names: tuple
    proof: dict
    mapping: object
    plan: object
    object_matrix: object


def _canonical_mapping(rig, packet, mapping, unit_scale):
    """Fixed reference frame from packet + native data, never visible C world.

    The normal mapping above still verifies the live object's size/units.
    Here a second deterministic fit uses *data-local Rest positions* only.
    It derives the required positive object scale and fixes its rotation and
    translation. Seeking A, including animated object Root motion, cannot
    change this independent B input or the sampled local value hash.
    """
    import numpy as np
    from mathutils import Matrix
    ua = _host('unity_animation')
    bound = [(name, i) for name, i in mapping.indices.items()
             if packet['bones'][i]['restSource'] == 'bindpose']
    source = np.asarray([tuple(ua._matrix(packet['bones'][i]['rest'], name).translation)
                         for name, i in bound], dtype=np.float64)
    local = np.asarray([tuple(rig.data.bones[name].matrix_local.translation) for name, _i in bound], dtype=np.float64)
    sc, lc = source.mean(axis=0), local.mean(axis=0)
    x, y = source - sc, local - lc
    u, singular, vt = np.linalg.svd(x.T @ y)
    rotation = u @ vt
    local_scale = float(singular.sum() / (x * x).sum())
    _require(math.isfinite(local_scale) and local_scale > 0 and
             math.isfinite(unit_scale) and unit_scale > 0,
             'Canonical B reference has invalid Rest units.')
    object_scale = 1. / (local_scale * unit_scale)
    world = Matrix.Diagonal((object_scale, object_scale, object_scale, 1.))
    affine = np.eye(4)
    affine[:3, :3] = rotation.T / unit_scale
    affine[:3, 3] = lc * object_scale - sc @ rotation / unit_scale
    conversion = Matrix(affine.tolist())
    rests = {name: world @ rig.data.bones[name].matrix_local for name in mapping.indices}
    for name, index in mapping.indices.items():
        delta = conversion @ ua._matrix(packet['bones'][index]['rest'], name).translation - rests[name].translation
        _require(delta.length * unit_scale <= .0002,
                 'Canonical B reference does not preserve the strict native bind position: ' + name)
    return ua.Mapping(dict(mapping.indices), conversion, mapping.error_metres, rests, mapping.controls), world


def validate_packet_composition(spec, packet):
    """Bind prepared B to its sealed DTO, with explicit local-new template IDs.

    This pure check authenticates no native new Action. resolve_scope also
    requires the real persisted row or registration-owned creation proof for
    that branch. A, weapon, timing, Rest and scope never inherit new-ID liberty.
    """
    spec = contract.validate(spec)
    _require(type(packet) is dict and type(packet.get('composition')) is dict,
             'B requires its complete sealed prepared packet composition.')
    sealed = contract.validate(packet['composition'])
    if spec['editNewActionId']:
        identifiers = {'compositionId', 'editWorkItemId', 'editNewActionId'}
        _require({key: value for key, value in spec.items() if key not in identifiers}
                 == {key: value for key, value in sealed.items() if key not in identifiers},
                 'Local new B bind-template composition changed outside its actual new IDs.')
    else:
        _require(spec == sealed,
                 'B composition differs from its sealed source packet; prepare a genuine variant.')
    return spec


def resolve_scope(context, rig, spec, packet, *, new_creation=None):
    spec = validate_packet_composition(spec, packet)
    if spec['editNewActionId']:
        _host('animation_worklist_new').authenticate_composition_bind_template(
            context, rig, spec, packet, creation=new_creation)
    else:
        _require(new_creation is None, 'A source B cannot use local new Action creation proof.')
    ua = _host('unity_animation')
    target = _host('animation_worklist_target')
    controls = _host('unity_animation_controls')
    limb = _host('limb_ik')
    fk = _host('limb_ik_fk')
    torso_module, spine_module = _host('torso_controls'), _host('spine_ik_fk')
    # Do not update a scene or leave Original to make a failed proof pass.
    mapping = _mapping_for_scope(rig, packet, context.scene.unit_settings.scale_length)
    chosen = resolve_packet_paths(spec, packet, mapping.indices)
    mapping, object_matrix = _canonical_mapping(rig, packet, mapping, context.scene.unit_settings.scale_length)
    plan = mapping.controls
    view = target.validation_rest_view(rig, mapping.indices)
    kwargs = {'original_rest': view, 'source_rest': view} if view is not None else {}
    inventory = limb._validate_inventory(rig, **kwargs)
    source_kw = {'source_rest': view} if view is not None else {}
    torso = torso_module.validate(rig, inventory=inventory, **source_kw)
    spine = spine_module.validate(rig, inventory=inventory, **source_kw)
    groups, identities, admission = [], {}, []
    for key, record in inventory['rigs'].items():
        groups.append(('limb ' + str(key), record['chain']))
        if set(record['chain']) & set(chosen):
            _require(fk.mode_for_rig(rig, record) == 'FK',
                     'Direct upperbody editing requires an exact FK limb; match FK first.')
            admission.append((record['target'].name, fk.PROPERTY, 0.))
        for native in record['chain']:
            identities[native] = 'limb-fk:' + record['rig_id'] + ':' + native
    if torso:
        groups.append(('Torso shared bend', torso['sources']))
        for native in torso['sources']:
            identities[native] = 'torso-fk:' + torso['id'] + ':' + native
    if spine and set(spine['sources']) & set(chosen):
        _require(spine_module.mode_for_rig(rig) == 'FK',
                 'Direct upperbody editing requires exact FK Spine Controls; match FK first.')
        groups.append(('Spine IK/FK', spine['sources']))
        admission.append((spine['chest'], spine_module.PROPERTY, 0.))
    require_complete_groups(chosen, groups)
    # Owned eye/root/foot or delegated Dress never become editable via a path.
    if plan:
        _require(not set(chosen) & (set(plan.eyes) | set(plan.delegated_names)),
                 'Composition cannot edit Eye or delegated Dress inputs.')
    redirects = plan.redirects if plan else {}
    inputs = {name: redirects.get(name, name) for name in chosen}
    _require(len(set(inputs.values())) == len(inputs), 'Several native bones share one edit input.')
    ids = {identities.get(name, 'native-fk:' + chosen[name]['path']) for name in chosen}
    _require(not spec['editableControlIds'] or set(spec['editableControlIds']) == ids,
             'editableControlIds must exactly identify the validated effective FK inputs.')
    for grip in spec['gripTargets']:
        _require(grip['rigBonePath'] in spec['allowedBonePaths'] and
                 (not grip['controlId'] or grip['controlId'] in ids),
                 'Grip target does not bind an authenticated editable FK input.')
    defaults, canonical = {}, {}
    for pb in rig.pose.bones:
        canonical.update(_defaults(pb))
    for name in inputs.values():
        defaults.update(_defaults(rig.pose.bones[name]))
    fixed = tuple(sorted((name, prop, float(value))
                         for (name, prop), value in (plan.switches.items() if plan else ())))
    # No inherited physics controls may pick up A or current C defaults.
    skirt = _host('skirt_rig')
    for source in skirt.shared_sources(rig):
        record = skirt.read_record(source)
        fixed += ((record['controls']['waist'], 'physics_influence', 0.),)
    # Only a shared bend coupled to selected upperbody sources must be neutral
    # in authored B. Eye/foot/root and other complete out-of-mask curves remain
    # preserved in Custom and are omitted by the explicitly requested mask.
    auxiliary = {torso['bend']} if torso and set(torso['sources']) & set(chosen) else set()
    original = view.original_data if view is not None else rig.data
    proof = {'policy': POLICY, 'sourceRestSha256': spec['sourceRestSha256'],
             'nativePaths': sorted((name, chosen[name]['path'], chosen[name]['humanRole']) for name in chosen),
             'effectiveInputs': sorted(inputs.items()), 'editableControlIds': sorted(ids),
             'sourceRest': contract.digest(ua._rest_state(original)),
             'motionRest': contract.digest(ua._rest_state(rig.data)),
             'constraints': sorted(plan.constraint_keys) if plan else [],
             'drivers': sorted(plan.driver_keys) if plan else [],
             'canonicalInputs': sorted((path, index, value) for (path, index), value in canonical.items()),
             'fixedProperties': sorted(fixed)}
    proof['canonicalObjectMatrix'] = [float(value) for row in object_matrix for value in row]
    return Scope(tuple(sorted(defaults)), defaults, tuple(sorted(chosen)),
                 {name: chosen[name]['path'] for name in chosen}, inputs, tuple(sorted(ids)),
                 canonical, tuple(sorted(fixed)), tuple(sorted(admission)), tuple(sorted(auxiliary)),
                 proof, mapping, plan, object_matrix)


def validate_scope(context, rig, spec, packet, scope=None, *, new_creation=None):
    fresh = resolve_scope(context, rig, spec, packet, new_creation=new_creation)
    if scope is not None:
        _require(fresh.channels == scope.channels and fresh.defaults == scope.defaults
                 and fresh.proof == scope.proof,
                 'Composition effective inputs or protected Rest changed; reopen the prepared composition.')
    return fresh


def effective_channel_names(scope):
    """Public Unity native local channels; Euler/control RNA never leaks here."""
    return tuple(sorted(path + '|' + prop + '|' + str(index)
                        for path in scope.path_by_native.values()
                        for prop, count in (('localPosition', 3), ('localRotation', 4), ('localScale', 3))
                        for index in range(count)))
