"""Frozen 0.77.40 Rest proof oracle; extracted without rewriting expressions.

This fixture is pure Python source. Tests provide native-like stand-ins; it
does not stand in for real Blender/mathutils validation.
"""


# unity_animation.py SHA256 c8db791e3077c439552211d5fef3770a59c9630340528ef451ecdea5b7f697b4 / 43840 bytes


def _same_rest_bone(original, preview):
    """Connected is the only structural flag changed by our disposable data copy."""
    if (original.parent.name if original.parent else None) != (preview.parent.name if preview.parent else None):
        return False
    if any(getattr(original, field) != getattr(preview, field) for field in
           ("use_deform", "use_inherit_rotation", "inherit_scale", "use_local_location", "use_relative_parent")):
        return False
    if (original.head_local - preview.head_local).length > 1e-6 or (original.tail_local - preview.tail_local).length > 1e-6:
        return False
    original_matrix, preview_matrix = original.matrix_local, preview.matrix_local
    return max(abs(original_matrix[i][j] - preview_matrix[i][j])
               for i in range(4) for j in range(4)) <= 1e-6


def _rest_state(data):
    return {bone.name: {"parent": bone.parent.name if bone.parent else None,
                       "head": list(bone.head_local), "tail": list(bone.tail_local),
                       "matrix": [float(v) for row in bone.matrix_local for v in row],
                       **{field: getattr(bone, field) for field in
                          ("use_connect", "use_deform", "use_inherit_rotation", "inherit_scale",
                           "use_local_location", "use_relative_parent")}}
            for bone in data.bones}


_SAVED_REST_FIELDS = frozenset(('parent', 'head', 'tail', 'matrix', 'use_connect', 'use_deform',
    'use_inherit_rotation', 'inherit_scale', 'use_local_location', 'use_relative_parent'))


_SAVED_REST_VECTOR_LENGTHS = {'head': 3, 'tail': 3, 'matrix': 16}


@dataclass(frozen=True)
class _SavedRestState:
    names: frozenset
    rows: object


def _compile_saved_rest_state(saved):
    """Detach ordinary saved JSON Rest only; never compile a native proof.

    Unusual values retain the normal materializer/comparison path, including
    its exception order. No native ID, data pointer or current state is read.
    """
    if type(saved) is not dict or any(type(name) is not str for name in saved):
        return None
    rows = {}
    for name, fields in saved.items():
        if type(fields) is not dict or set(fields) != _SAVED_REST_FIELDS:
            return None
        row = {}
        for field, value in fields.items():
            if field in _SAVED_REST_VECTOR_LENGTHS:
                if type(value) is not list or len(value) != _SAVED_REST_VECTOR_LENGTHS[field]:
                    return None
                try:
                    if any(type(number) not in (int, float, bool) or not math.isfinite(number)
                           for number in value):
                        return None
                except (OverflowError, ValueError):
                    return None
                row[field] = tuple(value)
            else:
                if (type(value) not in (str, bool, int, float, type(None))
                        or isinstance(value, float) and not math.isfinite(value)):
                    return None
                row[field] = value
        rows[name] = MappingProxyType(row)
    return _SavedRestState(frozenset(rows), MappingProxyType(rows))


def _matches_saved_rest_state(current, saved):
    if saved.names != frozenset(current):
        return False
    for name, fields in current.items():
        if set(fields) != _SAVED_REST_FIELDS:
            return False
        row = saved.rows[name]
        for field, value in fields.items():
            old = row[field]
            if field in _SAVED_REST_VECTOR_LENGTHS:
                if len(old) != len(value) or any(abs(x - y) > 1e-6 for x, y in zip(old, value)):
                    return False
            elif old != value:
                return False
    return True


def _matches_rest_state(data, saved):
    current = _rest_state(data)
    if isinstance(saved, _SavedRestState):
        return _matches_saved_rest_state(current, saved)
    if not isinstance(saved, dict) or set(saved) != set(current):
        return False
    for name, fields in current.items():
        if not isinstance(saved[name], dict) or set(saved[name]) != set(fields):
            return False
        for field, value in fields.items():
            old = saved[name][field]
            if field in {"head", "tail", "matrix"}:
                if (not isinstance(old, list) or len(old) != len(value)
                        or any(not isinstance(x, (int, float)) or not math.isfinite(x)
                               or abs(x - y) > 1e-6 for x, y in zip(old, value))):
                    return False
            elif old != value:
                return False
    return True


# animation_worklist_target.py SHA256 55b1dad8c9bfd6986a34b5e4cfc6c120dba80fedb2c76f09d613ad80b17a57fd / 45088 bytes


def _prove_motion_data(original, motion, original_rest, motion_rest, mapped_names):
    from . import unity_animation as ua, limb_ik, skirt_rig
    if (not ua._matches_rest_state(original, original_rest)
            or not ua._matches_rest_state(motion, motion_rest)):
        raise ValueError('The saved artist or owned motion-copy Rest changed; no validation exception was granted.')
    names = frozenset(mapped_names)
    if set(motion.bones.keys()) != set(original.bones.keys()) or not names.issubset(original.bones.keys()):
        raise ValueError('The owned motion copy changed the artist bone inventory.')
    # Exported Hair source chains may carry hair_bones ownership. They are
    # authored packet bones, whereas generated controls and Dress are not.
    owner_keys = (limb_ik.OWNER_KEY, skirt_rig.OWNER_KEY)
    translated = set()
    for bone in motion.bones:
        protected = original.bones[bone.name]
        if not ua._same_rest_bone(protected, bone):
            raise ValueError('The owned motion copy changed Rest geometry, parents or inheritance: ' + bone.name)
        if bool(bone.use_connect) != bool(protected.use_connect):
            if (not protected.use_connect or bone.use_connect or bone.name not in names
                    or any(protected.get(key) is not None or bone.get(key) is not None for key in owner_keys)):
                raise ValueError('An unapproved connected-bone flag changed in the motion copy: ' + bone.name)
            translated.add(bone.name)
    return frozenset(translated)
