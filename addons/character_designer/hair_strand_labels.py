"""Display-only numbering over the complete, already validated logical list."""
import re


_GENERATED = re.compile(r'Hair(?: .+)? [0-9]+(?:\.[0-9]{2}| Segment [0-9]{2})(?:\.[LRC])?')


def logical_labels(entries):
    """Return detached labels in input order without changing Hair identities.

    Pass all logical entries before category filtering so row numbers remain
    stable across filters. Proven pairs already occupy one logical entry. The
    number is a display ordinal only, never a strand identity or matching key.
    Equal names are retained; duplicate keys are ambiguous and are refused.
    """
    if not isinstance(entries, (list, tuple)):
        raise ValueError('Hair display entries must be an ordered sequence.')
    seen, result = set(), []
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError('Hair display entries must be detached records.')
        key, original = entry.get('key'), entry.get('bone_label')
        if (type(key) is not str or not key.strip()
                or type(original) is not str or not original.strip()):
            raise ValueError('Hair display identity or name is incomplete.')
        if key in seen:
            raise ValueError('Hair display entry keys are duplicated; refresh the strand list.')
        seen.add(key)
        label = ('Hair ' + str(number) if _GENERATED.fullmatch(original)
                 else str(number) + ' · ' + original)
        result.append(dict(key=key, number=number, label=label))
    return tuple(result)
