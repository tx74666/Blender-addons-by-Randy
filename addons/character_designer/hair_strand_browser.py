"""Read-only Hair strand listing and explicit organizational categories.

Categories reuse saved motion-profile groups, while preserving every effective
motion value. Browsing never initializes metadata, claims lifecycle identity,
stops previews, or edits native artist data.
"""
import copy
import hashlib
import json

from . import hair_bones_rig as hair
from . import hair_motion_lifecycle as lifecycle
from . import hair_motion_profiles as profiles
from . import hair_resegment as resegment
from . import hair_strand_registry as registry

GROUPS = profiles.GROUPS


class HairStrandBrowserError(ValueError):
    """A stale or incomplete browser request cannot safely change categories."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _id(value):
    try:
        name, session_uid = value.name, value.session_uid
    except (AttributeError, ReferenceError) as exc:
        raise HairStrandBrowserError('Hair browser object identity is unavailable.') from exc
    if not isinstance(name, str) or not name or type(session_uid) is not int:
        raise HairStrandBrowserError('Hair browser object identity is incomplete.')
    return {'name': name, 'session_uid': str(session_uid)}


def _raw(source, key):
    if key not in source:
        return {'missing': True}
    raw = source.get(key)
    if not isinstance(raw, str):
        raise HairStrandBrowserError('Saved Hair browser metadata must be a JSON string.')
    return {'sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest()}


def _fingerprint(source, rig, root, data):
    return {'source': _id(source), 'rig': _id(rig), 'root': _id(root),
            'source_uid': data['source_uid'], 'topology': data['topology'],
            'binding': _raw(source, hair.RECORD_KEY),
            'registry': _raw(source, registry.REGISTRY_KEY),
            'profile': _raw(source, profiles.PROFILE_KEY)}


def _default_profile(data):
    return {'version': profiles.VERSION, 'source_uid': data['source_uid'],
            'registry_topology': data['topology'],
            'group_defaults': copy.deepcopy(profiles.DEFAULTS),
            'strands': {item['strand_id']: {'group': 'UNASSIGNED',
                        'overrides': {}, 'sync_mirror': True}
                        for item in data['strands']}}


def _rows(data, profile):
    by_id = {item['strand_id']: item for item in data['strands']}
    return tuple({'signature': item['signature'], 'strand_id': item['strand_id'],
                  'bone_label': item['bones'][0], 'bones': tuple(item['bones']),
                  'group': profile['strands'][item['strand_id']]['group'],
                  'count': len(item['bones']), 'side': item['side'],
                  'order': item['order'], 'pair_id': item['pair_id'],
                  'pair_proof': item.get('pair_proof', 'NONE'),
                  'mirror_id': item['mirror_id'],
                  'mirror_signature': (by_id[item['mirror_id']]['signature']
                                       if item['mirror_id'] is not None else None),
                  'sync_mirror': profile['strands'][item['strand_id']]['sync_mirror']}
                 for item in sorted(data['strands'], key=lambda strand: strand['order']))


def _state(source):
    root = resegment.root_source(source)
    # Reject malformed raw properties before native validation. Explicit absence
    # differs from an invalid present value; no reader repairs either case.
    raw_before = tuple(_raw(source, key) for key in
                       (hair.RECORD_KEY, registry.REGISTRY_KEY, profiles.PROFILE_KEY))
    record = hair._read_records(source)
    if record is None:
        raise HairStrandBrowserError('Choose an existing bound Hair mesh.')
    source_uid = lifecycle.source_uid_for_bind(source, record)
    data = registry.read(source, validate=True)
    if data is None:
        data, live_record, _geometry = registry._live(source, check_layers=True)
        if live_record != record:
            raise HairStrandBrowserError('Hair binding changed while reading its strands.')
        # Only actual saved native-Mirror provenance establishes a pair here.
        if record.get('mirror_layout'):
            registry._mirror_provenance(data, record, source)
        registry._validate_links(data)
        registry._validate_mesh_pairs(data, _geometry)
    if data['source_uid'] != source_uid:
        raise HairStrandBrowserError('Hair strand ownership differs from its native binding.')
    profile = profiles.read(source, registry=data)
    if profile is None:
        profile = _default_profile(data)
    profiles._validate(profile, data)
    rig = source.get(hair.RIG_KEY)
    fingerprint = _fingerprint(source, rig, root, data)
    if raw_before != tuple(fingerprint[key] for key in ('binding', 'registry', 'profile')):
        raise HairStrandBrowserError('Hair metadata changed while reading its strands.')
    return {'rows': _rows(data, profile), 'fingerprint': fingerprint}, data, profile


def strict_snapshot(source):
    """Validate native ownership and return detached rows without any writes."""
    snapshot, _data, _profile = _state(source)
    return snapshot


def list_rows(source):
    """List all owned strands in registry order, including their saved category."""
    return strict_snapshot(source)['rows']


def _signatures(signatures, rows):
    if (not isinstance(signatures, (list, tuple, set, frozenset)) or not signatures
            or any(not isinstance(item, str) or not item for item in signatures)
            or len(signatures) != len(set(signatures))):
        raise HairStrandBrowserError('Choose one or more distinct Hair strands.')
    chosen = set(signatures)
    if chosen - {item['signature'] for item in rows}:
        raise HairStrandBrowserError('A chosen Hair strand disappeared; refresh the list.')
    return tuple(item['signature'] for item in rows if item['signature'] in chosen)


def _restore(source, saved):
    failures = []
    for key, (present, raw) in saved.items():
        try:
            if present:
                source[key] = raw
            elif key in source:
                del source[key]
            if (key in source) != present or present and source.get(key) != raw:
                raise HairStrandBrowserError('The original Hair metadata was not restored.')
        except Exception as exc:
            failures.append(str(exc))
    if failures:
        raise HairStrandBrowserError('Hair category rollback needs attention: ' + '; '.join(failures))


def assign_category(context, source, signatures, group, *, expected_fingerprint=None):
    """Assign checked strands only, preserving effective motion and pair intent.

    A synchronized pair requires both signatures explicitly. Same-group requests
    are no-ops, including on a source whose optional metadata is still missing.
    Only a missing registry and the updated profile can be persisted.
    """
    if not isinstance(group, str) or group not in GROUPS:
        raise HairStrandBrowserError('Choose Front, Side, Back or Unassigned.')
    snapshot, data, profile = _state(source)
    selected = _signatures(signatures, snapshot['rows'])
    if expected_fingerprint is not None:
        try:
            matches = (isinstance(expected_fingerprint, dict)
                       and _json(expected_fingerprint) == _json(snapshot['fingerprint']))
        except (TypeError, ValueError):
            matches = False
        if not matches:
            raise HairStrandBrowserError('Hair browser scope changed; refresh the strand list.')
    changed = tuple(signature for signature in selected
                    if next(row for row in snapshot['rows']
                            if row['signature'] == signature)['group'] != group)
    if not changed:
        return {**snapshot, 'changed': False, 'changed_signatures': (), 'group': group}
    selected_set = set(selected)
    by_signature = {row['signature']: row for row in snapshot['rows']}
    for signature in changed:
        row = by_signature[signature]
        partner = row['mirror_signature']
        if row['sync_mirror'] and partner not in (None, signature) and partner not in selected_set:
            raise HairStrandBrowserError('Check both synchronized mirror strands before changing their category.')
    if source.library or source.override_library or not source.is_editable:
        raise HairStrandBrowserError('Make Hair local and editable before changing categories.')
    # This guard reads the complete source/version family. It cannot stop a
    # solver or create/clear lifecycle or recovery metadata.
    resegment.guard_switch(context, source)
    updated = copy.deepcopy(profile)
    for signature in changed:
        strand_id = by_signature[signature]['strand_id']
        effective = profiles._effective_validated(profile, strand_id)
        item = updated['strands'][strand_id]
        item['overrides'] = {key: copy.deepcopy(effective[key])
                             for key in (*profiles.RANGES, 'depth')}
        item['group'] = group
    profiles._validate(updated, data)
    registry_raw = _json(data)
    profile_raw = _json(updated)
    # Recheck immediately before the first write, after the family guard.
    current = _fingerprint(source, source.get(hair.RIG_KEY), resegment.root_source(source), data)
    if current != snapshot['fingerprint']:
        raise HairStrandBrowserError('Hair browser scope changed before saving; refresh the list.')
    saved = {key: (key in source, source.get(key))
             for key in (registry.REGISTRY_KEY, profiles.PROFILE_KEY)}
    try:
        if not saved[registry.REGISTRY_KEY][0]:
            source[registry.REGISTRY_KEY] = registry_raw
            if source.get(registry.REGISTRY_KEY) != registry_raw:
                raise HairStrandBrowserError('The Hair strand registry could not be saved.')
        source[profiles.PROFILE_KEY] = profile_raw
        if source.get(profiles.PROFILE_KEY) != profile_raw:
            raise HairStrandBrowserError('The Hair category could not be saved.')
        result = strict_snapshot(source)
    except Exception as exc:
        try:
            _restore(source, saved)
        except Exception as rollback:
            raise HairStrandBrowserError(str(rollback)) from exc
        raise
    return {**result, 'changed': True, 'changed_signatures': changed, 'group': group}

