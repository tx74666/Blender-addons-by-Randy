"""Detached logical Hair entries over strictly validated physical strand rows.

This view does not discover symmetry. It joins only reciprocal L/R registry
links carrying an existing recognized pair proof. Native chains, motion profile
settings and their synchronization choices remain independent physical data.
"""

PROVEN_PAIRS = frozenset({'MIRROR', 'TOPOLOGY', 'GEOMETRY', 'GRAPH', 'MANUAL'})


class HairStrandEntriesError(ValueError):
    """Incomplete or stale logical membership cannot safely address Hair."""


def _text(value):
    return isinstance(value, str) and bool(value)


def _row(value):
    """Copy only primitive browser fields, without retaining native pointers."""
    if not isinstance(value, dict):
        raise HairStrandEntriesError('Hair strand rows must be detached records.')
    if (any(not _text(value.get(key)) for key in
            ('signature', 'strand_id', 'bone_label', 'group'))
            or type(value.get('order')) is not int or value['order'] < 0
            or type(value.get('count')) is not int or value['count'] < 1
            or not _text(value.get('side'))
            or value.get('side') not in {'L', 'R', 'C', 'U'}):
        raise HairStrandEntriesError('Hair strand identity or display data is incomplete.')
    bones = value.get('bones')
    if (not isinstance(bones, (list, tuple)) or not bones
            or any(not _text(name) for name in bones)
            or len(set(bones)) != len(bones)
            or len(bones) != value['count'] or value['bone_label'] != bones[0]):
        raise HairStrandEntriesError('Hair strand bone membership or per-side count is incomplete.')
    row = {key: value[key] for key in
           ('signature', 'strand_id', 'bone_label', 'group', 'count', 'side', 'order')}
    row['bones'] = tuple(bones)
    for key in ('pair_id', 'mirror_id', 'mirror_signature'):
        item = value.get(key)
        if item is not None and not _text(item):
            raise HairStrandEntriesError('Hair strand pair links must be detached identities.')
        row[key] = item
    proof = value.get('pair_proof', 'NONE')
    if not _text(proof):
        raise HairStrandEntriesError('Hair strand pair proof must be a detached identifier.')
    row['pair_proof'] = proof
    if 'sync_mirror' in value:
        if type(value['sync_mirror']) is not bool:
            raise HairStrandEntriesError('Hair strand motion synchronization state is invalid.')
        row['sync_mirror'] = value['sync_mirror']
    return row


def _reciprocal(first, second, pair_uses):
    return (second is not None and first is not second
            and {first['side'], second['side']} == {'L', 'R'}
            and first['pair_proof'] in PROVEN_PAIRS
            and second['pair_proof'] == first['pair_proof']
            and first['pair_id'] is not None
            and second['pair_id'] == first['pair_id']
            and pair_uses[first['pair_id']] == 2
            and first['mirror_id'] == second['strand_id']
            and second['mirror_id'] == first['strand_id']
            and first['mirror_signature'] == second['signature']
            and second['mirror_signature'] == first['signature'])


def build_entries(rows):
    """Return a deterministic logical view, preserving every physical member.

    Missing, unknown or nonreciprocal pair proof leaves a strand separate. Basic
    identity ambiguity raises instead of dropping or guessing members. ``count``
    is the common per-side count, or ``None`` for mixed counts; it is never the
    sum of a pair. ``counts`` and ``groups`` preserve each member's own values.
    """
    if not isinstance(rows, (list, tuple)):
        raise HairStrandEntriesError('Hair strand rows must be an ordered sequence.')
    ordered = sorted((_row(row) for row in rows), key=lambda row: row['order'])
    seen = {key: set() for key in ('signature', 'strand_id', 'order')}
    seen_bones, pair_uses = set(), {}
    for row in ordered:
        for key, values in seen.items():
            if row[key] in values:
                raise HairStrandEntriesError('Hair strand identities or order are duplicated.')
            values.add(row[key])
        if seen_bones.intersection(row['bones']):
            raise HairStrandEntriesError('Hair strand bone ownership overlaps.')
        seen_bones.update(row['bones'])
        if row['pair_id'] is not None:
            pair_uses[row['pair_id']] = pair_uses.get(row['pair_id'], 0) + 1
    by_id = {row['strand_id']: row for row in ordered}
    entries, consumed = [], set()
    for row in ordered:
        if row['signature'] in consumed:
            continue
        other = by_id.get(row['mirror_id'])
        paired = _reciprocal(row, other, pair_uses)
        members = tuple(sorted((row, other), key=lambda item: item['order'])) if paired else (row,)
        counts = tuple(item['count'] for item in members)
        groups = tuple(item['group'] for item in members)
        entries.append({
            'key': ('pair:' + row['pair_id']) if paired else ('strand:' + row['strand_id']),
            'signatures': tuple(item['signature'] for item in members),
            'signature': row['signature'], 'strand_id': row['strand_id'],
            'bone_label': row['bone_label'],
            'bones': tuple(name for item in members for name in item['bones']),
            'counts': counts, 'count': counts[0] if len(set(counts)) == 1 else None,
            'groups': groups, 'group': groups[0] if len(set(groups)) == 1 else 'MIXED',
            'side': 'L/R' if paired else row['side'], 'order': row['order'],
            'pair_id': row['pair_id'], 'member_count': len(members), 'members': members,
        })
        consumed.update(item['signature'] for item in members)
    return tuple(entries)


def _membership(entries):
    if not isinstance(entries, (list, tuple)):
        raise HairStrandEntriesError('Hair entries must be an ordered sequence.')
    known_keys, known_signatures, ordered = set(), set(), []
    for entry in entries:
        if not isinstance(entry, dict) or not _text(entry.get('key')):
            raise HairStrandEntriesError('Hair entry identity is incomplete.')
        signatures = entry.get('signatures')
        if (not isinstance(signatures, (tuple, list)) or not signatures
                or any(not _text(item) for item in signatures)
                or len(set(signatures)) != len(signatures)
                or entry['key'] in known_keys or known_signatures.intersection(signatures)):
            raise HairStrandEntriesError('Hair entry membership is incomplete or ambiguous.')
        known_keys.add(entry['key'])
        known_signatures.update(signatures)
        ordered.append((entry['key'], tuple(signatures)))
    return ordered, known_keys, known_signatures


def _chosen(values):
    if (not isinstance(values, (tuple, list, set, frozenset))
            or any(not _text(item) for item in values)
            or len(values) != len(set(values))):
        raise HairStrandEntriesError('Choose distinct Hair identities.')
    return set(values)


def normalize_signatures(entries, signatures):
    """Close a physical selection over logical entries in deterministic order.

    One checked/native-selected side therefore addresses both members of a
    proven pair. Unknown stale signatures raise; an empty selection stays empty.
    """
    ordered, _keys, known = _membership(entries)
    chosen = _chosen(signatures)
    if chosen - known:
        raise HairStrandEntriesError('A chosen Hair strand disappeared; refresh the list.')
    return tuple(signature for _key, members in ordered if chosen.intersection(members)
                 for signature in members)


def expand_keys(entries, keys):
    """Expand checked logical keys to every underlying physical signature."""
    ordered, known, _signatures = _membership(entries)
    chosen = _chosen(keys)
    if chosen - known:
        raise HairStrandEntriesError('A chosen Hair entry disappeared; refresh the list.')
    return tuple(signature for key, members in ordered if key in chosen
                 for signature in members)
