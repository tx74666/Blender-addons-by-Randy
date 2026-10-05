"""Persistent per-strand motion settings; never edits bones, weights or meshes.

Mirror links synchronize settings only. Each physical strand remains its own
chain. Normalized depth controls are independent of the bone segment count.
The settings are a behavior contract, not native Magica parameters.
"""
import copy
import json
import math

PROFILE_KEY = 'character_designer_hair_motion_v1'
VERSION = 1
GROUPS = ('FRONT', 'SIDE', 'BACK', 'UNASSIGNED')
RANGES = {'recovery': (0.0, 1.0), 'damping': (0.0, 1.0),
          'gravity': (0.0, 20.0), 'stretch': (0.0, 1.0)}
DEPTH_RANGES = {**RANGES, 'mass': (0.01, 10.0)}


def _preset(recovery, damping, gravity):
    return {'recovery': recovery, 'damping': damping, 'gravity': gravity,
            'stretch': 0.0, 'depth': [
                {'position': 0.0, 'recovery': recovery, 'damping': damping,
                 'mass': 1.0, 'gravity': gravity},
                {'position': 1.0, 'recovery': recovery * 0.65,
                 'damping': damping, 'mass': 1.0, 'gravity': gravity}]}


DEFAULTS = {'FRONT': _preset(0.9, 0.85, 1.0),
            'SIDE': _preset(0.7, 0.7, 4.0),
            'BACK': _preset(0.45, 0.55, 7.0),
            'UNASSIGNED': _preset(0.7, 0.7, 4.0)}


class HairMotionProfileError(ValueError):
    pass


def _registry(source, registry):
    if registry is None:
        from . import hair_strand_registry
        registry = hair_strand_registry.read(source, validate=True)
    if not registry or registry.get('version') != 1:
        raise HairMotionProfileError('Initialize the strand registry first.')
    strands = registry.get('strands', [])
    ids = [item.get('strand_id') for item in strands]
    if not ids or not all(isinstance(value, str) and value for value in ids) or len(ids) != len(set(ids)):
        raise HairMotionProfileError('The strand registry has invalid or duplicate identities.')
    return registry


def _number(value, limits, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise HairMotionProfileError(f'{label} must be a finite number.')
    if not limits[0] <= value <= limits[1]:
        raise HairMotionProfileError(f'{label} must be between {limits[0]} and {limits[1]}.')
    return float(value)


def _depth(values):
    if not isinstance(values, list) or not 2 <= len(values) <= 24:
        raise HairMotionProfileError('Depth needs 2 to 24 ordered controls including root and tip.')
    result = []
    previous = -1.0
    for value in values:
        if not isinstance(value, dict) or set(value) != {'position', 'recovery', 'damping', 'mass', 'gravity'}:
            raise HairMotionProfileError('Each depth control needs position, recovery, damping, mass and gravity.')
        position = _number(value['position'], (0.0, 1.0), 'Depth position')
        if position <= previous:
            raise HairMotionProfileError('Depth positions must increase from root to tip.')
        result.append({'position': position, **{key: _number(value[key], DEPTH_RANGES[key], key)
                                               for key in ('recovery', 'damping', 'mass', 'gravity')}})
        previous = position
    if result[0]['position'] != 0.0 or result[-1]['position'] != 1.0:
        raise HairMotionProfileError('Keep depth endpoints at 0 (root) and 1 (tip).')
    return result


def _settings(values, *, partial=False):
    if not isinstance(values, dict):
        raise HairMotionProfileError('Motion settings must be an object.')
    allowed = set(RANGES) | {'depth'}
    if set(values) - allowed or (not partial and set(values) != allowed):
        raise HairMotionProfileError('Unknown or incomplete motion settings.')
    return {key: _depth(value) if key == 'depth' else _number(value, RANGES[key], key)
            for key, value in values.items()}


def _validate(record, registry):
    if not isinstance(record, dict) or record.get('version') != VERSION:
        raise HairMotionProfileError('The saved Hair motion profile is unsupported.')
    if record.get('source_uid') != registry['source_uid']:
        raise HairMotionProfileError('Motion settings belong to another Hair source.')
    if record.get('registry_topology') != registry['topology']:
        raise HairMotionProfileError('Hair topology changed; explicitly reconcile the strand registry first.')
    defaults = record.get('group_defaults')
    if not isinstance(defaults, dict) or set(defaults) != set(GROUPS):
        raise HairMotionProfileError('The motion group defaults are incomplete.')
    for settings in defaults.values():
        _settings(settings)
    records = record.get('strands')
    expected = {item['strand_id'] for item in registry['strands']}
    if not isinstance(records, dict) or set(records) != expected:
        raise HairMotionProfileError('Motion settings and captured strands differ; reconcile explicitly.')
    for item in records.values():
        if not isinstance(item, dict) or set(item) != {'group', 'overrides', 'sync_mirror'}:
            raise HairMotionProfileError('A strand motion record is malformed.')
        if item['group'] not in GROUPS or type(item['sync_mirror']) is not bool:
            raise HairMotionProfileError('A strand motion group or mirror option is invalid.')
        _settings(item['overrides'], partial=True)
    for strand in registry['strands']:
        other = strand.get('mirror_id')
        if not other or other == strand['strand_id']:
            continue
        first, second = records[strand['strand_id']], records.get(other)
        if second is None or first['sync_mirror'] != second['sync_mirror']:
            raise HairMotionProfileError('The pair mirror settings differ; reconcile them explicitly.')
        if first['sync_mirror']:
            left = {**defaults[first['group']], **first['overrides'], 'group': first['group']}
            right = {**defaults[second['group']], **second['overrides'], 'group': second['group']}
            if left != right:
                raise HairMotionProfileError('A synchronized pair has different settings; reconcile explicitly.')
    return record


def read(source, *, registry=None):
    registry = _registry(source, registry)
    raw = source.get(PROFILE_KEY)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HairMotionProfileError('The saved Hair motion settings cannot be read.') from exc
    return copy.deepcopy(_validate(record, registry))


def _write(source, record, registry):
    _validate(record, registry)
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    old = source.get(PROFILE_KEY)
    try:
        source[PROFILE_KEY] = raw
        if source.get(PROFILE_KEY) != raw:
            raise HairMotionProfileError('Hair motion settings could not be saved.')
    except Exception:
        if old is None:
            if PROFILE_KEY in source:
                del source[PROFILE_KEY]
        else:
            source[PROFILE_KEY] = old
        raise
    return copy.deepcopy(record)


def initialize(source, *, registry=None):
    registry = _registry(source, registry)
    existing = read(source, registry=registry)
    if existing is not None:
        return existing
    record = {'version': VERSION, 'source_uid': registry['source_uid'],
              'registry_topology': registry['topology'], 'group_defaults': copy.deepcopy(DEFAULTS),
              'strands': {item['strand_id']: {'group': 'UNASSIGNED', 'overrides': {}, 'sync_mirror': True}
                          for item in registry['strands']}}
    return _write(source, record, registry)


def reconcile(source, *, registry=None):
    """Explicit registry refresh; retain settings by stable ID, never geometry."""
    registry = _registry(source, registry)
    raw = source.get(PROFILE_KEY)
    if raw is None:
        return initialize(source, registry=registry)
    try:
        record = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HairMotionProfileError('The saved Hair motion settings cannot be reconciled.') from exc
    if (not isinstance(record, dict) or record.get('version') != VERSION
            or not isinstance(record.get('strands'), dict)):
        raise HairMotionProfileError('The saved Hair motion settings have an unsupported structure.')
    if record.get('source_uid') != registry['source_uid']:
        raise HairMotionProfileError('Refusing to reuse settings from another Hair source.')
    expected = {item['strand_id'] for item in registry['strands']}
    previous = record.get('strands', {})
    for item in previous.values():
        if (not isinstance(item, dict) or set(item) != {'group', 'overrides', 'sync_mirror'}
                or item.get('group') not in GROUPS or type(item.get('sync_mirror')) is not bool):
            raise HairMotionProfileError('A saved strand has malformed motion settings; restore its record.')
        _settings(item['overrides'], partial=True)
    if set(previous) - expected:
        raise HairMotionProfileError('A configured strand disappeared; restore it before reconciling settings.')
    record['registry_topology'] = registry['topology']
    record['strands'] = {strand_id: previous.get(strand_id, {'group': 'UNASSIGNED', 'overrides': {},
                                                          'sync_mirror': True}) for strand_id in expected}
    # A newly proven pair must not silently replace either author's settings.
    # Differing old settings opt out reciprocally; enabling sync is explicit.
    for strand in registry['strands']:
        other = strand.get('mirror_id')
        if not other or other == strand['strand_id']:
            continue
        first, second = record['strands'][strand['strand_id']], record['strands'][other]
        if first != second:
            first['sync_mirror'] = second['sync_mirror'] = False
    return _write(source, record, registry)


def _effective_validated(record, strand_id):
    """Detach one strand from a record validated by this current operation."""
    if record is None or strand_id not in record['strands']:
        raise HairMotionProfileError('Initialize motion settings before editing this strand.')
    strand = record['strands'][strand_id]
    settings = copy.deepcopy(record['group_defaults'][strand['group']])
    settings.update(copy.deepcopy(strand['overrides']))
    return {**settings, 'group': strand['group'], 'sync_mirror': strand['sync_mirror']}


def effective(source, strand_id, *, registry=None, record=None):
    registry = _registry(source, registry)
    record = read(source, registry=registry) if record is None else _validate(record, registry)
    return _effective_validated(record, strand_id)


def effective_all(source, *, registry=None, record=None):
    registry = _registry(source, registry)
    record = read(source, registry=registry) if record is None else _validate(record, registry)
    return {item['strand_id']: _effective_validated(record, item['strand_id'])
            for item in registry['strands']}


def _targets(registry, record, strand_id, sync=None):
    items = {item['strand_id']: item for item in registry['strands']}
    if strand_id not in items or strand_id not in record['strands']:
        raise HairMotionProfileError('The selected strand no longer exists.')
    targets = [strand_id]
    mirror = items[strand_id].get('mirror_id')
    if mirror == strand_id and items[strand_id].get('side') == 'C':
        return targets
    enabled = record['strands'][strand_id]['sync_mirror'] if sync is None else sync
    if enabled and mirror is not None:
        other = items.get(mirror)
        if other is None or other.get('mirror_id') != strand_id or mirror == strand_id:
            raise HairMotionProfileError('The saved mirror pair is incomplete; no settings were changed.')
        if items[strand_id].get('pair_id') != other.get('pair_id') or not other.get('pair_id'):
            raise HairMotionProfileError('The saved mirror identity is inconsistent.')
        targets.append(mirror)
    return targets


def update(source, strand_id, *, group=None, overrides=None, sync_mirror=None, registry=None):
    registry = _registry(source, registry)
    record = read(source, registry=registry)
    if record is None:
        raise HairMotionProfileError('Initialize motion settings first.')
    if group is not None and group not in GROUPS:
        raise HairMotionProfileError('Choose Front, Side, Back or Unassigned.')
    if sync_mirror is not None and type(sync_mirror) is not bool:
        raise HairMotionProfileError('Mirror synchronization must be enabled or disabled.')
    settings = _settings(overrides, partial=True) if overrides is not None else None
    if settings is not None and 'depth' not in settings and any(key in settings for key in ('recovery', 'damping', 'gravity')):
        current = effective(source, strand_id, registry=registry, record=record)
        knots = copy.deepcopy(current['depth'])
        for key in ('recovery', 'damping', 'gravity'):
            if key in settings:
                for knot in knots:
                    knot[key] = min(DEPTH_RANGES[key][1], max(DEPTH_RANGES[key][0],
                        knot[key] * settings[key] / current[key] if current[key] else settings[key]))
        settings['depth'] = _depth(knots)
    targets = _targets(registry, record, strand_id, sync_mirror)
    # The switch describes a pair. Turning it off must also turn off its other
    # side, without applying settings to that side when synchronization is off.
    flag_targets = _targets(registry, record, strand_id, True) if sync_mirror is not None else targets
    for target in targets:
        item = record['strands'][target]
        if group is not None:
            item['group'] = group
        if settings is not None:
            item['overrides'].update(copy.deepcopy(settings))
    if sync_mirror is not None:
        for target in flag_targets:
            record['strands'][target]['sync_mirror'] = sync_mirror
    return _write(source, record, registry)


def restore_group_defaults(source, strand_id, *, registry=None, whole_group=False):
    registry = _registry(source, registry)
    record = read(source, registry=registry)
    if record is None:
        raise HairMotionProfileError('Initialize motion settings first.')
    group = record['strands'][strand_id]['group']
    targets = [key for key, item in record['strands'].items() if item['group'] == group] if whole_group else [strand_id]
    targets = set(targets)
    for key in tuple(targets):
        targets.update(_targets(registry, record, key))
    for key in targets:
        record['strands'][key]['overrides'] = {}
    return _write(source, record, registry)


def apply_to_group(source, strand_id, *, registry=None):
    """Use this strand as its group's saved default and clear group overrides."""
    registry = _registry(source, registry)
    record = read(source, registry=registry)
    settings = effective(source, strand_id, registry=registry, record=record)
    group = settings.pop('group')
    settings.pop('sync_mirror')
    record['group_defaults'][group] = settings
    targets = {key for key, item in record['strands'].items() if item['group'] == group}
    for key in tuple(targets):
        targets.update(_targets(registry, record, key))
    for key in targets:
        record['strands'][key]['group'] = group
        record['strands'][key]['overrides'] = {}
    return _write(source, record, registry)


def depth_sample(settings, position):
    """Linear interpolation in physical chain depth, independent of segments."""
    knots = _depth(settings['depth'])
    position = _number(position, (0.0, 1.0), 'Depth position')
    for first, second in zip(knots, knots[1:]):
        if first['position'] <= position <= second['position']:
            fraction = (position - first['position']) / (second['position'] - first['position'])
            return {key: first[key] + (second[key] - first[key]) * fraction
                    for key in ('recovery', 'damping', 'mass', 'gravity')}
    return {key: knots[-1][key] for key in ('recovery', 'damping', 'mass', 'gravity')}


def suggested_groups(registry):
    """Coarse editable defaults for the rig's local -Y front convention.

    Geometry suggests motion groups only. It never establishes mirror partners.
    Proven pairs use their shared feature average; existing group assignments
    are not changed by reading these suggestions.
    """
    features = {}
    anchors = []
    for item in registry['strands']:
        states = item.get('rest', [])
        if not states:
            raise HairMotionProfileError('Group suggestions require saved ordered Rest geometry.')
        length = sum(math.dist(state['head'], state['tail']) for state in states)
        features[item['strand_id']] = (length, -float(states[0]['head'][1]))
        anchor = item.get('anchor')
        if anchor:
            anchors.append(math.dist(anchor['head'], anchor['tail']))
    longest = max(length for length, forward in features.values())
    long_cutoff = max(longest * 0.4, (max(anchors) * 1.5 if anchors else 0))
    most_forward = max(0.0, max(forward for length, forward in features.values()))
    result = {}
    for item in registry['strands']:
        length, forward = features[item['strand_id']]
        other = item.get('mirror_id')
        if other in features and other != item['strand_id']:
            length = (length + features[other][0]) / 2
            forward = (forward + features[other][1]) / 2
        if length >= long_cutoff and longest > 0:
            group = 'BACK'
        elif most_forward > 0 and forward >= most_forward * 0.2:
            group = 'FRONT'
        else:
            group = 'SIDE'
        result[item['strand_id']] = group
    return result


def assign_suggested_groups(source, *, registry=None):
    """Explicit initial grouping; never replace an assigned artist group."""
    registry = _registry(source, registry)
    record = read(source, registry=registry)
    if record is None:
        raise HairMotionProfileError('Initialize the motion profile first.')
    suggestions = suggested_groups(registry)
    for strand_id, group in suggestions.items():
        if record['strands'][strand_id]['group'] == 'UNASSIGNED':
            targets = _targets(registry, record, strand_id)
            if any(record['strands'][other]['group'] != 'UNASSIGNED' for other in targets):
                continue
            for other in targets:
                record['strands'][other]['group'] = group
    return _write(source, record, registry)
