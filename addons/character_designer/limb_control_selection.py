"""Bounded selection handoff after an explicit Arm IK/FK UI switch.

The matcher and animation handlers do not call this service. Selection resolves
from the selected bones' owned records; it never enumerates a rig or an Action.
Unavailable destinations leave the current selection and artist visibility alone.
"""

import json


def _limb():
    from . import limb_ik
    return limb_ik


def _pose_context(context, armature):
    return (context.mode == 'POSE' and armature is not None
            and context.view_layer.objects.active == armature)


def _chain(value):
    if isinstance(value, str):
        value = json.loads(value)
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or any(type(name) is not str or not name for name in value)
            or len(set(value)) != 3):
        return None
    return tuple(value)


def _checked_rig(armature, key, chain, target, pole, rig_id, api):
    if (key not in {('ARM', 'L'), ('ARM', 'R')} or not chain
            or type(rig_id) is not str or not rig_id
            or type(target) is not str or type(pole) is not str
            or not target or not pole or target == pole
            or target in chain or pole in chain):
        return None
    armature_id = armature.data.get(api.ARMATURE_ID_KEY)
    if not armature_id:
        return None
    for name in chain:
        pb = armature.pose.bones.get(name)
        if pb is None or pb.bone != armature.data.bones.get(name):
            return None
    for name, role in ((target, api.LIMB_SPEC['ARM']['target_role']), (pole, 'POLE')):
        bone = armature.data.bones.get(name)
        pb = armature.pose.bones.get(name)
        if (bone is None or pb is None or pb.bone != bone
                or not api._owned(bone, armature_id, role=role, rig_id=rig_id)
                or bone.get(api.KIND_KEY) != key[0]
                or bone.get(api.SIDE_KEY) != key[1]
                or _chain(bone.get(api.CHAIN_KEY)) != chain):
            return None
    return dict(chain=chain, target=target, pole=pole, rig_id=rig_id)


def _direct_chains(armature, api):
    """Read only the fixed Direct registry's chain names, never its Rest states."""
    try:
        if armature.data.get(api.SCHEMA_KEY) not in {
                api.LEGACY_DIRECT_PREROLL_SCHEMA, api.DIRECT_PREROLL_SCHEMA}:
            return {}
        raw = armature.data.get(api.DIRECT_REST_KEY, '')
        # Four three-bone entries normally take only a few KB. This optional
        # selection hint must not decode an unbounded substituted payload.
        if not isinstance(raw, str) or not raw or len(raw) > 128 * 1024:
            return {}
        payload = json.loads(raw)
        if (not isinstance(payload, dict)
                or payload.get('version') != api.DIRECT_PREROLL_RESULT_VERSION
                or not isinstance(payload.get('limbs'), dict)
                or len(payload['limbs']) > 4):
            return {}
        result, sides, members = {}, set(), set()
        for rig_id, entry in payload['limbs'].items():
            if type(rig_id) is not str or not rig_id or not isinstance(entry, dict):
                return {}
            key = (entry.get('kind'), entry.get('side'))
            chain = _chain(entry.get('chain'))
            if (key not in {('ARM', 'L'), ('ARM', 'R'), ('LEG', 'L'), ('LEG', 'R')}
                    or key in sides or chain is None or members.intersection(chain)):
                return {}
            sides.add(key)
            members.update(chain)
            if key[0] == 'ARM':
                for name in chain:
                    result[name] = (rig_id, key, chain)
        return result
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return {}


def _selected_rigs(armature, poses, api):
    result, ambiguous = {}, set()
    armature_id = armature.data.get(api.ARMATURE_ID_KEY)
    direct = _direct_chains(armature, api)
    for pose in poses:
        candidates = [(pose, None)]
        bone = pose.bone
        if (api._owned(bone, armature_id)
                and bone.get(api.KIND_KEY) == 'ARM'
                and bone.get(api.ROLE_KEY) in {api.LIMB_SPEC['ARM']['target_role'], 'POLE'}):
            chain = _chain(bone.get(api.CHAIN_KEY))
            if chain:
                candidates.extend((armature.pose.bones.get(name), None) for name in chain)
        elif pose.name in direct:
            expected = direct[pose.name]
            # Direct upper arms carry no constraint record. Its saved chain
            # locates the lower/wrist records without traversing child bones.
            candidates.extend((armature.pose.bones.get(name), expected)
                              for name in expected[2][1:])
        for candidate, expected in candidates:
            if candidate is None:
                continue
            try:
                records = api._constraint_registry(candidate, strict=True)
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
                continue
            for constraint_name, record in records.items():
                if not isinstance(record, dict):
                    continue
                key = (record.get('kind'), record.get('side'))
                chain = _chain(record.get('chain'))
                rig_id, role = record.get('rig_id'), record.get('role')
                if (key not in {('ARM', 'L'), ('ARM', 'R')}
                        or record.get('owner') != api.OWNER_VALUE
                        or record.get('version') != api.RIG_VERSION
                        or record.get('armature_id') != armature_id
                        or role not in {'IK', 'END_ROTATION', 'SOURCE_UPPER_ROTATION',
                                        'SOURCE_LOWER_ROTATION'}
                        or constraint_name != api._constraint_name(rig_id, role)
                        or not chain or candidate.name not in chain
                        or (expected is not None and expected != (rig_id, key, chain))
                        or pose.name not in {*chain, record.get('target'), record.get('pole')}):
                    continue
                checked = _checked_rig(armature, key, chain, record.get('target'),
                                       record.get('pole'), rig_id, api)
                if checked is None:
                    continue
                if key in result and checked != result[key]:
                    ambiguous.add(key)
                result[key] = checked
    return {key: rig for key, rig in result.items() if key not in ambiguous}


def capture(context, armature):
    """Capture selected names and their bounded owned Arm mappings, or None."""
    try:
        if not _pose_context(context, armature):
            return None
        poses = {pb.name: pb for pb in (context.selected_pose_bones or ())
                 if armature.pose.bones.get(pb.name) == pb and pb.select}
        selected = set(poses)
        active = armature.data.bones.active
        active_name = active.name if active else None
        active_pose = armature.pose.bones.get(active_name) if active_name else None
        if active_pose is not None:
            poses[active_name] = active_pose
            if active_pose.select:
                selected.add(active_name)
        return dict(armature=armature, selected=tuple(sorted(selected)), active=active_name,
                    rigs=_selected_rigs(armature, poses.values(), _limb()))
    except Exception:
        # The switch itself must remain usable if an optional selection hint is corrupt.
        return None


def _selectable(pose):
    bone = pose.bone
    if (bone.hide or bone.hide_select or getattr(pose, 'hide', False)
            or not getattr(bone, 'is_visible', True)
            or not getattr(pose, 'is_visible', True)):
        return False
    collections = tuple(getattr(bone, 'collections', ()))
    return (not collections or any(getattr(collection, 'is_visible_effectively',
                                           getattr(collection, 'is_visible', False))
                                   for collection in collections))


def handoff(context, armature, snapshot, mode, keys=None, inventory=None):
    """Swap only captured Arm selections; failures never cancel a committed switch.

    An optional already verified inventory can supply the standard ``rigs`` map.
    Otherwise capture's authenticated mappings avoid a second deep validation.
    Return the number of selected/active source names handed off.
    """
    before, active_before = {}, None
    try:
        if (not snapshot or snapshot.get('armature') != armature
                or not _pose_context(context, armature) or mode not in {'IK', 'FK'}):
            return 0
        api = _limb()
        rigs = snapshot['rigs'] if inventory is None else inventory['rigs']
        chosen = tuple(rigs) if keys is None else tuple(keys)
        selected, active = set(snapshot['selected']), snapshot['active']
        replacements = {}
        for key in chosen:
            if key not in {('ARM', 'L'), ('ARM', 'R')} or key not in rigs:
                continue
            rig = rigs[key]
            target = rig['target'] if isinstance(rig['target'], str) else rig['target'].name
            pole = rig['pole'] if isinstance(rig['pole'], str) else rig['pole'].name
            target_bone = armature.data.bones.get(target)
            checked = _checked_rig(armature, key, _chain(rig['chain']), target, pole,
                                   target_bone.get(api.RIG_ID_KEY) if target_bone else None, api)
            if checked is None or (inventory is None and checked != rig):
                continue
            destination = checked['chain'][2] if mode == 'FK' else target
            destination_pose = armature.pose.bones.get(destination)
            if destination_pose is None or not _selectable(destination_pose):
                continue
            sources = (target, pole) if mode == 'FK' else checked['chain']
            for name in sources:
                if name in selected or name == active:
                    replacements[name] = destination
        if not replacements:
            return 0
        active_before = armature.data.bones.active
        names = set(replacements) | set(replacements.values())
        before = {name: bool(armature.pose.bones[name].select) for name in names}
        destinations = {destination for name, destination in replacements.items() if name in selected}
        for name in destinations:
            armature.pose.bones[name].select = True
        for name in replacements:
            if name in selected and name not in destinations:
                armature.pose.bones[name].select = False
        if active in replacements:
            name = replacements[active]
            armature.data.bones.active = armature.data.bones[name]
            # Assigning active may select; retain an originally unselected active.
            armature.pose.bones[name].select = name in destinations or before[name]
        return len(replacements)
    except Exception:
        # Restore only the bounded participants; no pose, visibility or Action writes.
        try:
            if before:
                armature.data.bones.active = active_before
                for name, selected in before.items():
                    armature.pose.bones[name].select = selected
        except Exception:
            pass
        return 0
