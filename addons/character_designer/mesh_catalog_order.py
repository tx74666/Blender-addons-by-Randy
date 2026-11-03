"""Pure swap plans for named native mesh attribute catalogs."""


def name_sort_swaps(names):
    """Sort unique names with the fewest arbitrary position swaps.

    Callers keep their existing native swap and membership preservation rules.
    A direct swap avoids moving a full vertex payload through adjacent slots.
    """
    current = list(names)
    positions = {name: index for index, name in enumerate(current)}
    if len(positions) != len(current):
        raise ValueError('Catalog names must be unique.')
    result = []
    for target, wanted in enumerate(sorted(current, key=lambda name: (name.casefold(), name))):
        index = positions[wanted]
        if index == target:
            continue
        displaced = current[target]
        result.append((displaced, wanted))
        current[target], current[index] = wanted, displaced
        positions[wanted], positions[displaced] = target, index
    return tuple(result)
