"""Current native tags for presentation only; never operation admission.

Each call returns a fresh, deliberately incomplete UI DTO. It does not prove
Rest, constraints, drivers, recovery owners or animation compatibility. The
DataBone references are for this draw only and must not be retained or passed
to an operation in place of that operation's full inventory validation.
"""
import json


_KEYS = (('ARM', 'L'), ('ARM', 'R'), ('LEG', 'L'), ('LEG', 'R'))
_MAX_IDENTIFIER = 256
_MAX_CHAIN_JSON = 4096
_MAX_FOOT_JSON = 256 * 1024
_FOOT_RECORD = 'character_designer_foot_controls_v1'


def _limb():
    # Imported only after limb_ik has finished loading; no backend is invoked.
    from . import limb_ik
    return limb_ik


def _identifier(value):
    return type(value) is str and 0 < len(value) <= _MAX_IDENTIFIER


def _chain(raw, bones, limb):
    if type(raw) is not str or not 0 < len(raw) <= _MAX_CHAIN_JSON:
        raise limb.LimbIKError('Limb display chain metadata is unavailable.')
    try:
        names = json.loads(raw)
    except (ValueError, TypeError, RecursionError) as exc:
        raise limb.LimbIKError('Limb display chain metadata is unreadable.') from exc
    if (type(names) is not list or len(names) != 3
            or any(not _identifier(name) for name in names)
            or len(set(names)) != 3 or any(bones.get(name) is None for name in names)):
        raise limb.LimbIKError('Limb display chain names are missing or ambiguous.')
    return tuple(names)


def _foot_metadata(armature, rigs, limb):
    """Project only the optional Advanced display fields from bounded JSON.

    This intentionally does not call Foot Controls' recovery reader, inspect its
    mechanisms, or borrow an admission result. The existing suspended-side set
    is read only to preserve which optional buttons are hidden during an
    operation. Explicit operators still decide whether a control can be used.
    """
    raw = armature.data.get(_FOOT_RECORD)
    if raw is None:
        return
    if type(raw) is not str or not 0 < len(raw) <= _MAX_FOOT_JSON:
        raise limb.LimbIKError('Foot Controls display metadata is unavailable.')
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError, RecursionError) as exc:
        raise limb.LimbIKError('Foot Controls display metadata is unreadable.') from exc
    if (type(payload) is not dict or type(payload.get('version')) is not int
            or payload['version'] != 1 or type(payload.get('legs')) is not dict
            or len(payload['legs']) > 2):
        raise limb.LimbIKError('Foot Controls display metadata is unsupported.')
    pointer = armature.as_pointer()
    suspended = limb.foot_controls._SUSPENDED
    for side, record in payload['legs'].items():
        if (pointer, side) in suspended:
            continue
        base = rigs.get(('LEG', side))
        if (side not in ('L', 'R') or base is None or type(record) is not dict
                or type(record.get('version')) is not int or record['version'] != 1
                or record.get('side') != side or record.get('rig_id') != base['rig_id']
                or record.get('chain') != list(base['chain'])
                or record.get('target') != base['target'].name
                or record.get('rotation_direction') not in (None, 'LEGACY', 'NATURAL')):
            raise limb.LimbIKError('Foot Controls display metadata does not match this leg.')
        base['foot_controls'] = {
            'presentation_only': True,
            'version': 1,
            'side': side,
            'rig_id': base['rig_id'],
            'rotation_direction': record.get('rotation_direction'),
        }


def inventory(armature, *, include_foot=False):
    """Read current Target/Pole pairs without authenticating a control graph.

    Missing limbs are allowed. An incomplete, duplicated or mismatched observed
    pair fails closed. Non-owned bones and other owned helper roles are ignored.
    No cache survives a call, so native rename/delete/tag changes are read again.
    """
    limb = _limb()
    if (armature is None or getattr(armature, 'type', None) != 'ARMATURE'
            or getattr(armature, 'data', None) is None):
        raise limb.LimbIKError('Select an armature to display its limb controls.')
    data, bones = armature.data, armature.data.bones
    armature_id = data.get(limb.ARMATURE_ID_KEY, '')
    schema = data.get(limb.SCHEMA_KEY, limb.LEGACY_SCHEMA)
    rotation_version = data.get(limb.TARGET_ROTATION_VERSION_KEY, 0)
    if (armature_id != '' and not _identifier(armature_id)):
        raise limb.LimbIKError('Limb display armature identity is invalid.')
    if (type(schema) is not int or schema not in (
            limb.LEGACY_SCHEMA, limb.ENHANCED_SCHEMA, limb.ROLL_DECOUPLED_SCHEMA,
            limb.LEGACY_DIRECT_PREROLL_SCHEMA, limb.DIRECT_PREROLL_SCHEMA)
            or type(rotation_version) is not int
            or rotation_version not in (0, limb.TARGET_ROTATION_VERSION)):
        raise limb.LimbIKError('Limb display schema is unsupported.')
    pairs = {}
    for bone in bones:
        if bone.get(limb.OWNER_KEY) != limb.OWNER_VALUE:
            continue
        role = bone.get(limb.ROLE_KEY)
        if role not in ('HAND_IK', 'FOOT_IK', 'POLE'):
            continue
        kind, side = bone.get(limb.KIND_KEY), bone.get(limb.SIDE_KEY)
        rig_id = bone.get(limb.RIG_ID_KEY)
        version = bone.get(limb.VERSION_KEY)
        if (not armature_id or type(version) is not int or version != limb.RIG_VERSION
                or bone.get(limb.ARMATURE_ID_KEY) != armature_id
                or (kind, side) not in _KEYS or not _identifier(rig_id)
                or role == 'HAND_IK' and kind != 'ARM'
                or role == 'FOOT_IK' and kind != 'LEG'):
            raise limb.LimbIKError('Limb display control tags are inconsistent.')
        pair = pairs.setdefault((kind, side), {})
        field = 'pole' if role == 'POLE' else 'target'
        if field in pair:
            raise limb.LimbIKError('Limb display has duplicate Target or Pole controls.')
        pair[field] = (bone, rig_id, _chain(bone.get(limb.CHAIN_KEY), bones, limb))
    rigs, used_ids = {}, set()
    for key in _KEYS:
        pair = pairs.get(key)
        if pair is None:
            continue
        if set(pair) != {'target', 'pole'}:
            raise limb.LimbIKError('Limb display is missing its Target or Pole.')
        target, pole = pair['target'], pair['pole']
        if target[1:] != pole[1:] or target[1] in used_ids:
            raise limb.LimbIKError('Limb display pair identity or chain is ambiguous.')
        used_ids.add(target[1])
        rigs[key] = {'target': target[0], 'pole': pole[0],
                     'chain': target[2], 'rig_id': target[1]}
    if include_foot:
        _foot_metadata(armature, rigs, limb)
    return {'presentation_only': True, 'armature_id': armature_id,
            'schema': schema, 'target_rotation_version': rotation_version,
            'rigs': rigs}
