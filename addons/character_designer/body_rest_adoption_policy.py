"""Pure eligibility for explicitly accepting current leg Rest geometry.

This module grants no Blender ownership, graph, Action compatibility or recovery
authority. Its caller must authenticate the existing Direct and Foot graphs,
the actual data owners, and any supplied companion names before using the result.
In particular, a companion name is an input to this policy, not proof of ownership.

The optional ``same_rest_bone`` comparator lets the runtime use the approved
``unity_animation._same_current_rest_bone`` float32 Vector boundary. The default
uses the same mathematical 1e-6 limits in pure Python; it is not a bit-identical
replacement for mathutils at the last-bit boundary. Production callers must pass
their source-authenticated native comparator. Schema and exact relation checks
always run first and cannot be overridden by that comparator.
"""
from dataclasses import dataclass
import math


REST_TOLERANCE = 1e-6
_FIELDS = frozenset((
    'parent', 'head', 'tail', 'matrix', 'use_connect', 'use_deform',
    'use_inherit_rotation', 'inherit_scale', 'use_local_location',
    'use_relative_parent',
))
_BOOLEANS = (
    'use_connect', 'use_deform', 'use_inherit_rotation',
    'use_local_location', 'use_relative_parent',
)
_RELATIONS = ('parent', 'inherit_scale', *_BOOLEANS)
_VECTOR_LENGTHS = {'head': 3, 'tail': 3, 'matrix': 16}
_INHERIT_SCALE = frozenset((
    'FULL', 'FIX_SHEAR', 'ALIGNED', 'AVERAGE', 'NONE', 'NONE_LEGACY',
))


class RestAdoptionPolicyError(ValueError):
    """A malformed snapshot or change outside the explicitly accepted scope."""


@dataclass(frozen=True)
class RestChanges:
    native_changed: frozenset
    companion_changed: frozenset


def _name(value):
    return type(value) is str and bool(value)


def _snapshot(value, label):
    """Validate and detach an ordinary complete native-Rest snapshot."""
    if type(value) is not dict or not value or any(not _name(n) for n in value):
        raise RestAdoptionPolicyError(f'{label} must contain named native Rest bones.')
    result = {}
    names = set(value)
    for name, fields in value.items():
        prefix = f'{label} bone {name!r}'
        if type(fields) is not dict or set(fields) != _FIELDS:
            raise RestAdoptionPolicyError(f'{prefix} has an incomplete or unknown Rest field.')
        row = {}
        parent = fields['parent']
        if parent is not None and (not _name(parent) or parent not in names or parent == name):
            raise RestAdoptionPolicyError(f'{prefix} has an invalid parent.')
        row['parent'] = parent
        for field in _BOOLEANS:
            if type(fields[field]) is not bool:
                raise RestAdoptionPolicyError(f'{prefix} {field} must be an exact boolean.')
            row[field] = fields[field]
        scale = fields['inherit_scale']
        if type(scale) is not str or scale not in _INHERIT_SCALE:
            raise RestAdoptionPolicyError(f'{prefix} has an invalid inheritance mode.')
        row['inherit_scale'] = scale
        for field, length in _VECTOR_LENGTHS.items():
            vector = fields[field]
            if type(vector) is not list or len(vector) != length:
                raise RestAdoptionPolicyError(f'{prefix} {field} must have {length} components.')
            try:
                valid = all(type(x) in (int, float) and math.isfinite(x) for x in vector)
            except (OverflowError, ValueError):
                valid = False
            if not valid:
                raise RestAdoptionPolicyError(f'{prefix} {field} must contain finite numbers, not booleans.')
            row[field] = list(vector)
        result[name] = row
    # Parent relationships are exact and must describe a finite hierarchy, even
    # when a malformed old and current snapshot happen to contain the same cycle.
    for name in result:
        seen = {name}
        parent = result[name]['parent']
        while parent is not None:
            if parent in seen:
                raise RestAdoptionPolicyError(f'{label} has a parent cycle at {name!r}.')
            seen.add(parent)
            parent = result[parent]['parent']
    return result


def _scope(previous, leg_chains, owned_companions):
    if type(leg_chains) is not dict or set(leg_chains) != {'L', 'R'}:
        raise RestAdoptionPolicyError('Accepting leg Rest requires exactly the existing L and R chains.')
    native = set()
    for side in ('L', 'R'):
        chain = leg_chains[side]
        if type(chain) not in (list, tuple) or len(chain) not in (3, 4):
            raise RestAdoptionPolicyError(f'{side} leg needs three native chain bones and an optional toe.')
        if any(not _name(n) or n not in previous for n in chain) or len(set(chain)) != len(chain):
            raise RestAdoptionPolicyError(f'{side} leg has missing or duplicate native bones.')
        if native.intersection(chain):
            raise RestAdoptionPolicyError('The two native leg chains must not overlap.')
        for parent, child in zip(chain, chain[1:]):
            if previous[child]['parent'] != parent:
                raise RestAdoptionPolicyError(f'{side} leg does not retain its direct native hierarchy.')
        native.update(chain)
    if type(owned_companions) not in (tuple, list, set, frozenset):
        raise RestAdoptionPolicyError('Authenticated companion names must be an explicit collection.')
    companions = set()
    for name in owned_companions:
        if not _name(name) or name not in previous or name in companions:
            raise RestAdoptionPolicyError('An authenticated companion name is missing or repeated.')
        if name in native:
            raise RestAdoptionPolicyError('A generated companion cannot also be a native leg bone.')
        companions.add(name)
    return frozenset(native), frozenset(companions)


def _same_geometry(previous, current):
    return (
        math.dist(previous['head'], current['head']) <= REST_TOLERANCE
        and math.dist(previous['tail'], current['tail']) <= REST_TOLERANCE
        and max(abs(x - y) for x, y in zip(previous['matrix'], current['matrix'])) <= REST_TOLERANCE
    )


def classify_changes(previous, current, *, leg_chains, owned_companions=(),
                     same_rest_bone=None):
    """Return allowed geometry deltas; refuse any inventory/structural change.

    ``previous`` and ``current`` must be complete ``ua._rest_state`` dictionaries.
    The caller supplies both existing native chains, including a toe when one is
    owned by the validated Foot graph, and only independently authenticated
    companion names. No input object is written or returned by this function.
    """
    old = _snapshot(previous, 'Previous')
    now = _snapshot(current, 'Current')
    if old.keys() != now.keys():
        raise RestAdoptionPolicyError('Native bone inventory changed; leg Rest acceptance cannot add, delete or rename bones.')
    native, companions = _scope(old, leg_chains, owned_companions)
    if same_rest_bone is not None and not callable(same_rest_bone):
        raise RestAdoptionPolicyError('The native Rest comparator is not callable.')
    # Never allow a comparator to grant structural changes, including changes to
    # unselected bones. Validate the entire domain before comparing geometry.
    for name in sorted(old):
        for field in _RELATIONS:
            if old[name][field] != now[name][field]:
                raise RestAdoptionPolicyError(f'Native structure changed at {name!r} ({field}); preserve that edit for explicit review.')
    native_changed, companion_changed = set(), set()
    compare = same_rest_bone or _same_geometry
    for name in sorted(old):
        same = compare(old[name], now[name])
        if type(same) is not bool:
            raise RestAdoptionPolicyError('The native Rest comparator must return an exact boolean.')
        if same:
            continue
        if name in native:
            native_changed.add(name)
        elif name in companions:
            companion_changed.add(name)
        else:
            raise RestAdoptionPolicyError(f'Rest geometry changed outside the accepted leg scope at {name!r}.')
    return RestChanges(frozenset(native_changed), frozenset(companion_changed))
