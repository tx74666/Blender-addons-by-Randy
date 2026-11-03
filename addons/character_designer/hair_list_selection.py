"""Pure list-click selection plans; ranges follow the visible row order."""


def _keys(values, label):
    if isinstance(values, (str, bytes, dict, set, frozenset)):
        raise ValueError('The Hair ' + label + ' must be an ordered list of entry keys.')
    try:
        result = tuple(values)
    except TypeError as error:
        raise ValueError('The Hair ' + label + ' is unreadable.') from error
    if any(type(key) is not str or not key.strip() for key in result):
        raise ValueError('The Hair ' + label + ' contains an invalid entry key.')
    if len(set(result)) != len(result):
        raise ValueError('The Hair ' + label + ' contains duplicate entry keys.')
    return result


def plan(keys, visible_keys, selected_keys, target_key, anchor_key='', *,
         extend=False, toggle=False):
    """Return ordered selected keys and the range anchor without mutating inputs.

    A plain click replaces the selection, including hidden rows. Ctrl toggles
    one row. Shift replaces it with the visible anchor-to-target range;
    Ctrl+Shift adds that range. A hidden or stale anchor starts a new range.
    """
    keys = _keys(keys, 'entry list')
    visible = _keys(visible_keys, 'visible entry list')
    selected = _keys(selected_keys, 'selected entry list')
    known = set(keys)
    if not set(visible).issubset(known) or not set(selected).issubset(known):
        raise ValueError('The Hair selection contains an unknown entry; refresh its strand list.')
    if type(target_key) is not str or not target_key.strip() or target_key not in visible:
        raise ValueError('Choose a visible Hair entry; refresh its strand list.')
    if type(anchor_key) is not str or (anchor_key and not anchor_key.strip()):
        raise ValueError('The Hair selection anchor is unreadable.')
    if type(extend) is not bool or type(toggle) is not bool:
        raise ValueError('The Hair selection modifiers are unreadable.')

    if extend:
        anchor = anchor_key if anchor_key in visible else target_key
        first, last = sorted((visible.index(anchor), visible.index(target_key)))
        wanted = set(visible[first:last + 1])
        if toggle:
            wanted.update(selected)
    else:
        anchor = target_key
        wanted = set(selected) if toggle else set()
        if toggle and target_key in wanted:
            wanted.remove(target_key)
        else:
            wanted.add(target_key)
    return tuple(key for key in keys if key in wanted), anchor
