"""Protect artist drivers while clearing selected control transforms."""


def _guard_drivers(rig, allowed, owned=()):
    # Native influence/IK drivers remain validated by the inventory. Artist
    # transform and custom-property drivers on this reset's inputs cannot be
    # overwritten by a neutral-pose request.
    prefixes = tuple(rig.pose.bones[name].path_from_id() for name in allowed)
    for curve in getattr(rig.animation_data, 'drivers', ()):
        if (curve.data_path, curve.array_index) in owned:
            continue
        for prefix in prefixes:
            if (curve.data_path.startswith(prefix + '.') and
                    curve.data_path[len(prefix):].startswith(('.location', '.rotation_', '.scale'))
                    or curve.data_path.startswith(prefix + '[')):
                raise ValueError('An artist driver controls this chain; no channels were reset.')
