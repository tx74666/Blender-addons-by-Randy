"""Read-only selection of authenticated Hair chains for segment changes.

Selection never guesses a mirror partner or silently falls back to all strands.
The plain record helpers are independent of bpy so malformed and mixed selection
can be refused before an operator creates IDs or changes frames.
"""


class HairSelectionError(ValueError):
    pass


def _chain_map(chains):
    result, bone_map = {}, {}
    for chain in chains:
        try:
            signature, bones, count = chain['signature'], chain['bones'], chain['count']
            if (type(signature) is not str or not signature or signature in result
                    or not isinstance(bones, (list, tuple)) or not bones
                    or type(count) is not int or not 1 <= count <= 12
                    or len(bones) != count
                    or any(type(name) is not str or not name for name in bones)):
                raise ValueError()
            for name in bones:
                if name in bone_map:
                    raise ValueError()
                bone_map[name] = signature
            result[signature] = chain
        except (KeyError, TypeError, ValueError):
            raise HairSelectionError('The recorded Hair chains are incomplete or ambiguous.') from None
    if not result:
        raise HairSelectionError('Bind Hair before choosing strands to change.')
    return result, bone_map


def resolve_names(chains, selected_names):
    """Return record-ordered signatures for selected bones, once per whole chain."""
    chain_map, bone_map = _chain_map(chains)
    if isinstance(selected_names, (str, bytes)):
        raise HairSelectionError('Choose Hair bones in Pose Mode.')
    try:
        names = tuple(selected_names)
    except TypeError:
        raise HairSelectionError('Choose Hair bones in Pose Mode.') from None
    if not names:
        raise HairSelectionError('Select a bone from each Hair strand you want to change.')
    if any(type(name) is not str or name not in bone_map for name in names):
        raise HairSelectionError('The selection contains a bone outside this bound Hair.')
    chosen = {bone_map[name] for name in names}
    return tuple(signature for signature in chain_map if signature in chosen)


def summarize(chains, signatures=None):
    """Read chain counts and names for a fixed selection, without changing it."""
    chain_map, unused = _chain_map(chains)
    if signatures is None:
        signatures = tuple(chain_map)
    if isinstance(signatures, (str, bytes)):
        raise HairSelectionError('The saved Hair strand selection is unreadable.')
    try:
        signatures = tuple(signatures)
    except TypeError:
        raise HairSelectionError('The saved Hair strand selection is unreadable.') from None
    if (not signatures or any(type(signature) is not str or signature not in chain_map
                              for signature in signatures)
            or len(set(signatures)) != len(signatures)):
        raise HairSelectionError('The saved Hair strand selection no longer matches this binding.')
    chosen = set(signatures)
    rows = tuple(dict(signature=signature, bones=tuple(chain['bones']), count=chain['count'])
                 for signature, chain in chain_map.items() if signature in chosen)
    return dict(strands=len(rows), current_counts=tuple(sorted({row['count'] for row in rows})),
                chains=rows)


def binding(source):
    """Resolve an existing native binding; all writes remain in the service."""
    import bpy
    from . import hair_bones_rig as hair
    if not isinstance(source, bpy.types.Object) or source.type != 'MESH':
        raise HairSelectionError('Choose a bound Hair mesh or its Hair bones.')
    record = hair._read_records(source)
    rig = source.get(hair.RIG_KEY)
    if not record or not isinstance(rig, bpy.types.Object) or rig.type != 'ARMATURE':
        raise HairSelectionError('Bind Hair before changing its segment count.')
    _chain_map(record['chains'])
    return record, rig


def selected(context, source):
    """Authenticate selected native Pose bones, then resolve their whole chains."""
    from . import hair_bones_rig as hair
    if context.mode != 'POSE':
        raise HairSelectionError('Use Pose Mode and select the Hair strands to change.')
    record, rig = binding(source)
    selected_bones = tuple(context.selected_pose_bones or ())
    if not selected_bones:
        raise HairSelectionError('Select a bone from each Hair strand you want to change.')
    names = []
    for pose in selected_bones:
        bone = pose.bone
        if (pose.id_data is not rig or bone.get(hair.OWNER_KEY) != hair.OWNER_VALUE
                or bone.get(hair.SOURCE_KEY) is not source):
            raise HairSelectionError('Select only Hair bones from this one bound Hair mesh.')
        names.append(pose.name)
    signatures = resolve_names(record['chains'], names)
    by_name = {name: chain['signature'] for chain in record['chains'] for name in chain['bones']}
    if any(pose.bone.get(hair.SIGNATURE_KEY) != by_name[pose.name] for pose in selected_bones):
        raise HairSelectionError('A selected Hair bone no longer matches its recorded strand.')
    return signatures
