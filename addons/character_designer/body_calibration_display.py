"""Transient redraw cache. Never used to authorize Preview/Apply/Confirm/Generate."""
import json
from functools import lru_cache
import bpy
from bpy.app.handlers import persistent
from . import body_calibration as service, limb_ik

_values = {}
_serial = 0
_SETTINGS = ('preset', 'arm_axis', 'leg_axis', 'arm_direction', 'leg_direction',
             'allow_bend', 'minimum_bend', 'max_shift', 'max_length', 'pole_distance',
             'include_arms', 'include_legs', 'include_fingers', 'palm_reference',
             'palm_tilt', 'palm_plane_flip', 'palm_object', 'palm_face', 'palm_flip')


def clear(*_args):
    global _serial
    _serial += 1
    _values.clear()


def _value(value):
    if isinstance(value, bpy.types.ID): return value.as_pointer()
    if isinstance(value, (str, bool, int, float)) or value is None: return value
    return tuple(value)


@lru_cache(maxsize=24)
def _decoded(raw):
    """Private read-only metadata; never returned to a service/write caller."""
    try:
        value = json.loads(raw) if raw else {}
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        # The uncached service supplies the actual validation error.
        return {}


def _matrix(matrix):
    return tuple(value for row in matrix for value in row)


def _rest_token(rig):
    """Read both committed and live Edit Rest; direct RNA can skip graph updates."""
    edit = rig.mode == 'EDIT'
    bones = rig.data.edit_bones if edit else rig.data.bones
    tags = (limb_ik.OWNER_KEY, limb_ik.ROLE_KEY, limb_ik.KIND_KEY,
            limb_ik.SIDE_KEY, limb_ik.CHAIN_KEY, limb_ik.RIG_ID_KEY)
    return tuple((b.name, tuple(b.head if edit else b.head_local),
                  tuple(b.tail if edit else b.tail_local),
                  tuple((b.matrix if edit else b.matrix_local).col[2]),
                  b.parent.name if b.parent else '', b.use_connect, b.use_deform,
                  b.hide, getattr(b, 'lock', False), tuple(b.get(tag) for tag in tags))
                 for b in bones)


def _pose_token(context, rig, part):
    """Match the overlay's pose source; retain numeric tuples, never evaluated RNA."""
    source = rig if part == 'HANDS' else rig.evaluated_get(context.evaluated_depsgraph_get())
    return tuple((bone.name, _matrix(bone.matrix), tuple(bone.head), tuple(bone.tail))
                 for bone in source.pose.bones)


def _mesh_token(obj, vertices=(), faces=(), edges=()):
    """Fingerprint only reference elements, including same-count topology edits."""
    if obj is None: return None
    if obj.type != 'MESH': return (obj.as_pointer(), obj.type)
    mesh = obj.data
    keys = mesh.shape_keys
    basis = keys.reference_key if keys else None
    source = basis.data if basis else mesh.vertices
    def elements(sequence, indices, attribute):
        return tuple((index, tuple(getattr(sequence[index], attribute))
                      if 0 <= index < len(sequence) else None) for index in sorted(set(indices)))
    return (obj.as_pointer(), obj.name, obj.mode, mesh.as_pointer(), mesh.name,
            _matrix(obj.matrix_world), keys.as_pointer() if keys else None,
            basis.as_pointer() if basis else None,
            len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(source),
            elements(source, vertices, 'co'), elements(mesh.polygons, faces, 'vertices'),
            elements(mesh.edges, edges, 'vertices'))


def _palm_token(rig, data):
    s = service.settings(rig)
    if s.palm_reference == 'PLANE': return ()
    wanted = {}
    for saved in data.get('palms', {}).values():
        if not isinstance(saved, dict): continue
        evidence = saved.get('evidence', {})
        if not isinstance(evidence, dict): continue
        obj = bpy.data.objects.get(evidence.get('object', ''))
        face = evidence.get('face')
        if isinstance(face, int): wanted[(obj.as_pointer() if obj else None, face)] = (obj, face)
    if s.palm_reference == 'MESH' and s.palm_object:
        wanted[(s.palm_object.as_pointer(), s.palm_face)] = (s.palm_object, s.palm_face)
    result = []
    for obj, face in wanted.values():
        ids = (tuple(obj.data.polygons[face].vertices)
               if obj and obj.type == 'MESH' and 0 <= face < len(obj.data.polygons) else ())
        result.append((face, _mesh_token(obj, ids, (face,))))
    return tuple(result)


def _reference_indices(records):
    vertices, faces, edges = set(), set(), set()
    for raw in records:
        if not isinstance(raw, dict): continue
        basis = raw.get('basis', {})
        if isinstance(basis, dict):
            for row in basis.get('coordinates', ()):
                if isinstance(row, (tuple, list)) and row and isinstance(row[0], int): vertices.add(row[0])
        evidence = raw.get('local_evidence', {})
        if not isinstance(evidence, dict): continue
        for kind, rows in evidence.items():
            if not isinstance(rows, dict): continue
            for index in rows:
                try: (faces if kind == 'FACES' else edges).add(int(index))
                except (ValueError, TypeError): pass
    return vertices, faces, edges


def _finger_token(rig, data):
    if not service.settings(rig).include_fingers: return ()
    from . import finger_targets
    names = sorted(finger_targets.index(rig))
    if not names: return ()
    result = []
    resolved = data.get('finger_references', {})
    for obj in bpy.data.objects:
        if obj.type != 'MESH': continue
        bank = getattr(obj, 'character_designer_finger_bank', None)
        if not bank or not bank.survey or not any(m.type == 'ARMATURE' and m.object == rig for m in obj.modifiers): continue
        slots, references = [], []
        for name in names:
            slot = bank.slots.get(name)
            if slot is None:
                slots.append((name, None))
                continue
            guide = slot.guide
            slots.append((name, guide.record, guide.confirmed, guide.flip_bend, slot.bones, slot.error))
            references.append(_decoded(guide.record))
            saved = resolved.get(name, {}) if isinstance(resolved, dict) else {}
            if isinstance(saved, dict): references.append(saved.get('record', {}))
        vertices, faces, edges = _reference_indices(references)
        result.append((bank.survey, tuple(slots), _mesh_token(obj, vertices, faces, edges)))
    return tuple(result)


def token(context, rig, part=None):
    s = service.settings(rig)
    ui = limb_ik._settings(context)
    mapping = tuple(getattr(ui, limb_ik._field_name(k, side, role), '')
                    for k in limb_ik.KINDS for side in limb_ik.SIDES for role in limb_ik.ROLES) if ui else ()
    raw = rig.get(service.KEY, '')
    data = _decoded(raw)
    # Off-part geometry must not make every Hands/Arms overlay walk finger refs.
    references = (_palm_token(rig, data) if part in (None, 'HANDS') else (),
                  _finger_token(rig, data) if part in (None, 'FINGERS') else ())
    return (rig.as_pointer(), rig.data.as_pointer(), context.scene.as_pointer(),
            context.view_layer.as_pointer(), context.mode, context.scene.frame_current,
            context.scene.frame_subframe, tuple(_value(getattr(s, name)) for name in _SETTINGS),
            ui.armature.as_pointer() if ui and ui.armature else None, mapping,
            raw, _matrix(rig.matrix_world), _rest_token(rig), references)


def get(context, rig, name, build):
    part = name[1] if isinstance(name, tuple) and len(name) > 1 and name[1] in service.STORED_PARTS else None
    try:
        current = token(context, rig, part)
        if isinstance(name, tuple) and name[0] == 'segments' and rig.mode == 'POSE':
            current += (_pose_token(context, rig, part),)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, ReferenceError):
        # Malformed/deleted reference metadata must reach the original diagnostic,
        # never keep a previous successful display entry or break panel drawing.
        return build()
    cached = _values.get(name)
    if cached is not None and cached[0] == current: return cached[1]
    serial = _serial
    result = build()
    if serial == _serial:
        if name not in _values and len(_values) >= 24: _values.pop(next(iter(_values)))
        _values[name] = (current, result)
    return result


def status(context, rig, part):
    return get(context, rig, ('status', part), lambda: service.status(context, rig, part))


def readiness(context, rig, part, changes):
    # Pose/constraint/animation blockers are evaluated now, not from a Rest token.
    return service.apply_readiness(context, rig, changes)


@persistent
def _invalidate(*_args):
    # Scene/mesh/bone/constraint updates, including Undo and file replacement.
    # No RNA references or generated objects are retained in this cache.
    clear()


def unregister():
    clear()
    _decoded.cache_clear()
    for name in ('depsgraph_update_post', 'undo_post', 'redo_post', 'load_post'):
        handlers = getattr(bpy.app.handlers, name)
        for fn in tuple(handlers):
            if fn.__module__ == __name__ and fn.__name__ == '_invalidate': handlers.remove(fn)


def register():
    unregister()
    for name in ('depsgraph_update_post', 'undo_post', 'redo_post', 'load_post'):
        getattr(bpy.app.handlers, name).append(_invalidate)
