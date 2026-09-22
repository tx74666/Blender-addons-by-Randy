"""Explicit region mirror metadata sync; no handlers or bone/mesh writes.

The mesh mirror owns its existing geometry transaction. This optional callback
stages current, proven references for fully covered fingers and leaves partial
or unusable definitions alone. Metadata commit failures propagate so the outer
mesh transaction can roll back too.
"""
import copy
import json
import uuid

from mathutils import Matrix, Vector

from . import finger_bank as bank, finger_definition as definition
from . import finger_internal as internal, finger_loop_marks as marks

_UNAVAILABLE = (ValueError, KeyError, TypeError, IndexError, AttributeError)


def _same_plane(plan):
    try:
        plane = bank.plane(plan.obj)
        reflected = plane.inverted() @ Matrix.Diagonal((-1, 1, 1, 1)) @ plane
        return all(abs(a-b) <= 1e-6 for row, other in zip(reflected, plan.reflection)
                   for a, b in zip(row, other))
    except ValueError:
        return False


def _fresh_record(obj, bm, key, guide, candidate):
    """Reprove saved spatial intent against current evidence, not old indices."""
    record = copy.deepcopy(json.loads(guide.record))
    if guide.source != obj or not guide.use_basis or guide.bend_record:
        raise ValueError('This saved reference needs an independent review.')
    if record.get('kind') != 'MESH' or not record.get('internal'):
        raise ValueError('No saved internal reference.')
    body = internal.prepare_body(bm, candidate, record['basis']['path'])
    record['internal'].update(internal.verify(bm, body, record['internal']))
    faces = internal.support_faces(body)
    ids = sorted({v.index for face in faces for v in bm.faces[face].verts})
    coordinates = [[index, list(bm.verts[index].co)] for index in ids]
    record.update(body=body, input={'kind': 'VERTS', 'ids': ids},
                  support_input={'kind': 'FACES', 'ids': faces},
                  topology=definition._topology(bm), bank_key=key, capture_source=key,
                  key=definition._basis_name(obj), basis_key=definition._basis_name(obj))
    record.pop('start_input', None)
    record.pop('local_evidence', None)
    # A historical selected strip no longer provides current topology indices.
    # Keep its saved spatial path, but replace all live proof evidence above.
    if isinstance(record.get('surface'), dict): record['surface'].pop('origin', None)
    record['basis']['coordinates'] = coordinates
    record['current'] = copy.deepcopy(record['basis'])
    return bank.enrich(record, bm)


def prepare(plan):
    """Read current selected source evidence after mirror planning, in any mode."""
    obj = plan.obj
    state = obj.character_designer_finger_bank
    packet = dict(sources={}, unchanged=set(), enabled=bool(state.survey))
    if not packet['enabled']: return packet
    bm = definition._snapshot(obj, definition._basis_name(obj))
    try:
        try: report = bank.survey(None, obj, bm, force=True)
        except _UNAVAILABLE:
            packet['unchanged'].add(state.active)
            return packet
        selected = set(plan.source_faces)
        for key, candidate in report['candidates'].items():
            slot = state.slots.get(key)
            if not slot or not slot.guide.record: continue
            faces = set(candidate['faces'])
            if not faces & selected: continue
            if not faces <= selected or not _same_plane(plan):
                packet['unchanged'].add(key)
                continue
            try:
                record = _fresh_record(obj, bm, key, slot.guide, candidate)
                stored = marks.records(obj, key)
                recovered = {}
                for number, marker in stored.items():
                    if number not in {'1', '2'}: continue
                    vertices = marks._recover(bm, marker, int(number))
                    marks._ring(obj, bm, record, vertices)
                    if not {v.index for v in vertices} <= set(plan.source_vertices):
                        raise ValueError('The complete marked loop is outside the selection.')
                    recovered[number] = marks._marker(obj, key, vertices, record)
                packet['sources'][key] = dict(record=record, marks=recovered,
                    confirmed=slot.guide.confirmed, flip_bend=slot.guide.flip_bend)
            except _UNAVAILABLE:
                packet['unchanged'].add(key)
        return packet
    finally:
        bm.free()


def snapshot(obj):
    state = obj.character_designer_finger_bank
    return dict(survey=state.survey, needs_recheck=state.needs_recheck,
                status=state.status, bone_status=state.bone_status,
                slots={slot.name: ({field: getattr(slot.guide, field) for field in bank.FIELDS},
                                   slot.error, slot.bones) for slot in state.slots},
                marks=obj.get(marks.PROPERTY), has_marks=marks.PROPERTY in obj)


def restore(obj, saved):
    state = obj.character_designer_finger_bank
    from .finger_definition_ui import reference_write
    with reference_write():
        for index in reversed(range(len(state.slots))):
            if state.slots[index].name not in saved['slots']: state.slots.remove(index)
        for name, (fields, error, bones) in saved['slots'].items():
            slot = bank._slot(state, name)
            for field, value in fields.items(): setattr(slot.guide, field, value)
            slot.error, slot.bones = error, bones
        for field in ('survey', 'needs_recheck', 'status', 'bone_status'):
            setattr(state, field, saved[field])
    if saved['has_marks']: obj[marks.PROPERTY] = saved['marks']
    elif marks.PROPERTY in obj: del obj[marks.PROPERTY]


def _commit(obj, report, updates, saved_marks):
    state = obj.character_designer_finger_bank
    from .finger_definition_ui import reference_write
    with reference_write():
        if report is not None:
            state.survey, state.needs_recheck = json.dumps(report), ''
        for key, update in updates.items():
            slot = bank._slot(state, key)
            guide = slot.guide
            guide.source, guide.record = obj, json.dumps(update['record'])
            guide.revision, guide.use_basis = uuid.uuid4().hex, True
            guide.bend_source, guide.bend_record = None, ''
            guide.pending_source, guide.pending, guide.status = None, '', ''
            guide.flip_bend, guide.confirmed = update['flip_bend'], update['confirmed']
            slot.error, slot.bones = '', ''
    if updates:
        if saved_marks: obj[marks.PROPERTY] = json.dumps(saved_marks, separators=(',', ':'))
        elif marks.PROPERTY in obj: del obj[marks.PROPERTY]


def apply(context, plan, packet):
    """Run inside apply_plan's callback while the new mesh is in Object Mode."""
    obj = plan.obj
    if not packet['enabled']: return dict(synced=0, unchanged=0)
    saved = snapshot(obj)
    unchanged, updates = set(packet['unchanged']), {}
    saved_marks = copy.deepcopy(marks._saved(obj))
    bm = definition._snapshot(obj, definition._basis_name(obj))
    try:
        try: report = bank.survey(context, obj, bm, force=True)
        except _UNAVAILABLE:
            report = None
            unchanged.update(packet['sources'])
        for source, incoming in packet['sources'].items() if report is not None else ():
            target = source[:-1]+('R' if source[-1] == 'L' else 'L')
            try:
                candidate = report['candidates'].get(target)
                if candidate is None or not _same_plane(plan): raise ValueError('No proven opposite finger.')
                record = bank.remap_record(incoming['record'], bm,
                                          reflection=bank.plane(obj), target=candidate)
                record['bank_key'] = target
                record['internal'].update(internal.verify(bm, record['body'], record['internal']))
                reflected_marks = {}
                for number, marker in incoming['marks'].items():
                    reflected = dict(marker, coordinates=[list(plan.reflection @ Vector(p))
                                                         for p in marker['coordinates']])
                    vertices = marks._recover(bm, reflected, int(number))
                    marks._ring(obj, bm, record, vertices)
                    reflected_marks[number] = marks._marker(obj, target, vertices, record)
                updates[target] = dict(incoming, record=record)
                if reflected_marks: saved_marks[target] = reflected_marks
                else: saved_marks.pop(target, None)
            except _UNAVAILABLE:
                unchanged.add(source)
        try:
            _commit(obj, report, updates, saved_marks)
        except Exception:
            restore(obj, saved)
            raise
        return dict(synced=len(updates), unchanged=len(unchanged))
    finally:
        bm.free()
