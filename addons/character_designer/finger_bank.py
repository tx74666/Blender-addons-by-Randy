"""Independent finger-side definitions, owned by the mesh, not capture order.

All writes here are reference metadata. Detection never repairs the mesh.
"""
import copy
import hashlib
import json
import uuid
from types import SimpleNamespace

import bpy
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

from . import finger_definition as definition, finger_detect as detect

FIELDS = ('source', 'record', 'use_basis', 'revision', 'bend_source', 'bend_record',
          'flip_bend', 'pending_source', 'pending', 'confirmed', 'status')
PAIR_WARNING_VERSION = 2
PAIR_SYNC_WARNING = 'Sync both hands with Mirror Selected Region.'


def discard_legacy_view_errors():
    """Forget obsolete auto-monitor warnings, retaining every saved reference.

    Called on reload/load, never on geometry updates. These warnings are not
    proof of current geometry; explicit actions still validate before writing.
    """
    fragments = ('This finger surface changed beyond complete loop subdivision/dissolve',
                 'Reference faces/edges changed; recapture this finger only.',
                 'Reference points changed or became ambiguous; recapture this finger only.',
                 'The reference geometry changed. Capture the finger definition again.',
                 'Geometry changed: Recheck symmetry.')
    cleared = 0
    for obj in getattr(bpy.data, 'objects', ()):
        state = getattr(obj, 'character_designer_finger_bank', None)
        if state is None: continue
        for owner, fields in [(state, ('status', 'bone_status', 'needs_recheck'))] + [
                (owner, fields) for slot in state.slots if slot.guide.record
                for owner, fields in ((slot, ('error',)), (slot.guide, ('status',)))]:
            for field in fields:
                value = getattr(owner, field)
                if value and any(fragment in value for fragment in fragments):
                    setattr(owner, field, '')
                    cleared += 1
    return cleared


def active_object(context):
    if hasattr(context, 'finger_bank_object'): return context.finger_bank_object
    obj = getattr(context.scene, 'character_designer_finger_setup', None)
    # Do not show another character's definitions while editing a new mesh.
    editing = getattr(context, 'edit_object', None)
    current = getattr(context, 'object', None)
    mesh = editing if editing and editing.type == 'MESH' else current if current and current.type == 'MESH' else None
    if mesh:
        saved = getattr(mesh, 'character_designer_finger_bank', None)
        return mesh if saved and saved.survey else None
    return obj if obj and obj.type == 'MESH' else None


def active_state(context):
    explicit = getattr(context, 'finger_definition', None)
    if explicit is not None: return explicit
    obj = active_object(context)
    if obj:
        bank = obj.character_designer_finger_bank
        slot = bank.slots.get(bank.active)
        if slot: return slot.guide
    return None


def scoped(context, guide):
    return SimpleNamespace(scene=context.scene, finger_definition=guide)


def plane(obj):
    mirrors = [m for m in obj.modifiers if m.type == 'MIRROR' and m.use_axis[0] and m.mirror_object]
    return mirrors[0].mirror_object.matrix_world.inverted() @ obj.matrix_world if mirrors else Matrix.Identity(4)


def stamp(bm, obj, *, reader=None):
    topology = reader.topology(bm) if reader else definition._topology(bm)
    return hashlib.sha256((topology+repr([tuple(v.co) for v in bm.verts])+
                           repr(tuple(tuple(r) for r in plane(obj)))).encode()).hexdigest()


def _slot(bank, key):
    slot = bank.slots.get(key)
    if slot is None:
        slot = bank.slots.add()
        slot.name = key
    return slot


def configured(slot):
    """A detected body alone is not a captured reference that can fail."""
    return bool(slot and (slot.guide.record or slot.guide.pending or slot.guide.bend_record))


def clear(context, digit=None, *, side=None):
    """Forget one side's setup, retaining its mate and the detection census."""
    obj = active_object(context)
    if not obj: return
    bank = obj.character_designer_finger_bank
    digit = digit or bank.active.split('.')[0]
    side = side or display_side(bank)
    if digit not in detect.DIGITS or side not in ('L', 'R'): return
    key = f'{digit}.{side}'
    slot = bank.slots.get(key)
    from . import finger_definition_ui, finger_bone_tools, finger_flex
    if slot:
        with finger_definition_ui.reference_write():
            definition._clear_state(slot.guide)
            slot.error = slot.bones = ''
    if key == bank.active:
        bank.status = bank.bone_status = ''
        finger_flex.state(context).status = ''
    from . import finger_loop_marks, finger_loop_marks_ui
    finger_loop_marks.clear_key(obj, key)
    finger_loop_marks_ui.refresh(context)
    # Survey warnings describe the mesh and remain useful when recapturing.
    # They are explicit census evidence, independent of either saved guide.
    finger_definition_ui.redraw()
    finger_bone_tools.invalidate(context)
    finger_flex._visible = False


def _cycle(ids):
    ids = tuple(ids)
    return min(ids[i:]+ids[:i] for i in range(len(ids)))


def enrich(record, bm):
    specs = [record[name] for name in ('input', 'start_input', 'support_input') if name in record]
    evidence = {'FACES': {}, 'EDGES': {}}
    for spec in specs:
        if spec['kind'] == 'VERTS': continue
        seq = bm.faces if spec['kind'] == 'FACES' else bm.edges
        for i in spec['ids']: evidence[spec['kind']][str(i)] = [v.index for v in seq[i].verts]
    record['local_evidence'] = evidence
    return record


def _tree(bm):
    tree = KDTree(len(bm.verts))
    for v in bm.verts: tree.insert(v.co, v.index)
    tree.balance()
    return tree


def _remap_record_exact(record, bm, *, reflection=None, target=None):
    """Exact local correspondence across index changes; no nearest-finger guess."""
    record = copy.deepcopy(record)
    evidence = record.get('local_evidence')
    if evidence is None: raise ValueError('Capture this older reference again to establish local correspondence.')
    tree, mapping = _tree(bm), {}
    for i, coordinate in record['basis']['coordinates']:
        point = detect.reflect(coordinate, reflection) if reflection is not None else Vector(coordinate)
        candidates = tree.find_range(point, 1e-6)
        if len(candidates) != 1: raise ValueError('Reference points changed or became ambiguous; recapture this finger only.')
        mapping[i] = candidates[0][1]
    face_lookup = {_cycle(v.index for v in f.verts): f.index for f in bm.faces}
    edge_lookup = {frozenset(v.index for v in e.verts): e.index for e in bm.edges}
    index_maps = {'VERTS': mapping, 'FACES': {}, 'EDGES': {}}
    for domain in ('FACES', 'EDGES'):
        for i, indices in evidence[domain].items():
            mapped = [mapping[v] for v in indices]
            if reflection is not None: mapped.reverse()
            lookup = face_lookup if domain == 'FACES' else edge_lookup
            key = _cycle(mapped) if domain == 'FACES' else frozenset(mapped)
            if key not in lookup: raise ValueError('Reference faces/edges changed; recapture this finger only.')
            index_maps[domain][int(i)] = lookup[key]
    for name in ('input', 'start_input', 'support_input'):
        if name in record:
            spec = record[name]
            spec['ids'] = [index_maps[spec['kind']][i] for i in spec['ids']]
    if record.get('body'):
        body = record['body']
        body['rings'] = [[mapping[i] for i in row] for row in body['rings']]
        body['vertices'] = [mapping[i] for i in body['vertices']]
        body['faces'] = [index_maps['FACES'][i] for i in body['faces']]
        if reflection is not None:
            for name in ('root', 'tip'): body[name] = list(detect.reflect(body[name], reflection))
    for variant in ('basis', 'current'):
        sample = record[variant]
        sample['coordinates'] = [[mapping[i], list(detect.reflect(co, reflection)) if reflection is not None else co]
                                 for i, co in sample['coordinates']]
        if reflection is not None:
            sample['path'] = [list(detect.reflect(p, reflection)) for p in sample['path']]
            if sample.get('normal'):
                mirror = reflection.inverted() @ Matrix.Diagonal((-1, 1, 1, 1)) @ reflection
                sample['normal'] = list((mirror.to_3x3().inverted().transposed() @ Vector(sample['normal'])).normalized())
    if reflection is not None:
        for part in ('internal', 'surface'):
            if part in record:
                record[part]['path'] = [list(detect.reflect(p, reflection)) for p in record[part]['path']]
        centering = record.get('surface', {}).get('centering')
        if centering:
            centering['path'] = [list(detect.reflect(p, reflection)) for p in centering['path']]
            mirror = reflection.inverted() @ Matrix.Diagonal((-1, 1, 1, 1)) @ reflection
            centering['normal'] = list((mirror.to_3x3().inverted().transposed() @ Vector(centering['normal'])).normalized())
    if target is not None and not set(index_maps['VERTS'].values()) & set(target['vertices']):
        raise ValueError('Mirrored reference does not touch the opposite finger.')
    record['topology'] = definition._topology(bm)
    return enrich(record, bm)


def _row_sample(points, rows, tolerance=1e-6):
    """Prove one complete cross-section lies on one original quad band."""
    matches = []
    for band, (first, last) in enumerate(zip(rows, rows[1:])):
        directions = [b-a for a, b in zip(first, last)]
        length_squared = sum(d.length_squared for d in directions)
        if length_squared <= tolerance*tolerance: continue
        fraction = sum((p-a).dot(d) for p, a, d in zip(points, first, directions))/length_squared
        if fraction < -tolerance or fraction > 1+tolerance: continue
        fraction = max(0., min(1., fraction))
        if all((p-a.lerp(b, fraction)).length <= tolerance for p, a, b in zip(points, first, last)):
            matches.append((band, fraction))
    if not matches or any(abs((i+f)-(matches[0][0]+matches[0][1])) > tolerance for i, f in matches[1:]):
        raise ValueError('This finger surface changed beyond complete loop subdivision/dissolve; rebind this finger only.')
    return matches[0]


def _remap_ring_edits(record, bm, candidate=None):
    """Accept surface-equivalent whole-ring edits, never a nearest-surface fit.

    Root, tip and cap must remain exact. Every new row lies on the original
    rails, and every old row lies on the new rails. The reverse proof prevents
    dissolving a shape-defining bend or accepting a moved/deformed section.
    """
    from . import finger_internal, finger_range
    old = record.get('body')
    evidence = record.get('local_evidence')
    if not old or not record.get('internal') or not evidence:
        raise ValueError('This reference has no verified finger sleeve; rebind this finger only.')
    if candidate is None:
        found = _match_previous({'finger': old}, detect.census(bm, finger_range.quad_band))
        candidate = found.get('finger')
    if not candidate:
        raise ValueError('This finger body / closed tip is unavailable; rebind this finger only.')
    old_rows, new_rows = old['rings'], candidate['rings']
    if len(old_rows) < 3 or len(new_rows) < 3 or any(len(row) != len(old_rows[0]) for row in old_rows+new_rows):
        raise ValueError('The finger circumference or protected boundary changed; rebind this finger only.')
    coordinates = {i: Vector(co) for i, co in record['basis']['coordinates']}
    if any(i not in coordinates for row in old_rows for i in row):
        raise ValueError('The saved sleeve evidence is incomplete; rebind this finger only.')
    columns = []
    for vi in old_rows[0]:
        hits = [j for j, ni in enumerate(new_rows[0]) if (bm.verts[ni].co-coordinates[vi]).length <= 1e-6]
        if len(hits) != 1: raise ValueError('The protected finger root changed; rebind this finger only.')
        columns.append(hits[0])
    width = len(columns)
    if len(set(columns)) != width or not any(all((columns[(i+1) % width]-columns[i]) % width == direction % width
                                               for i in range(width)) for direction in (1, -1)):
        raise ValueError('The finger root correspondence is ambiguous; rebind this finger only.')
    new_rows = [[row[column] for column in columns] for row in new_rows]
    old_points = [[coordinates[i] for i in row] for row in old_rows]
    new_points = [[bm.verts[i].co.copy() for i in row] for row in new_rows]
    if any((a-b).length > 1e-6 for a, b in zip(old_points[-1], new_points[-1])):
        raise ValueError('The protected fingertip boundary changed; rebind this finger only.')
    samples = [_row_sample(row, old_points) for row in new_points]
    if any((j+g)-(i+f) <= 1e-6 for (i, f), (j, g) in zip(samples, samples[1:])):
        raise ValueError('The finger loops cross or duplicate a section; rebind this finger only.')
    for row in old_points: _row_sample(row, new_points)

    # Check oriented strip connectivity and exact cap/support faces outside it.
    old_faces = {int(i): ids for i, ids in evidence['FACES'].items()}
    by_vertices = {frozenset(ids): (i, ids) for i, ids in old_faces.items()}
    new_faces = {_cycle(v.index for v in face.verts): face.index for face in bm.faces}
    old_strip = set()
    winding = None
    for first, last in zip(old_rows, old_rows[1:]):
        for j in range(width):
            polygon = [first[j], last[j], last[(j+1) % width], first[(j+1) % width]]
            face = by_vertices.get(frozenset(polygon))
            if face is None: raise ValueError('The saved finger bands are incomplete; rebind this finger only.')
            direction = _cycle(face[1]) == _cycle(polygon)
            if winding is not None and direction != winding:
                raise ValueError('The saved finger band orientation is inconsistent.')
            winding = direction
            old_strip.add(face[0])
    for first, last in zip(new_rows, new_rows[1:]):
        for j in range(width):
            polygon = [first[j], last[j], last[(j+1) % width], first[(j+1) % width]]
            if not winding: polygon.reverse()
            if _cycle(polygon) not in new_faces:
                raise ValueError('The edited finger is not a complete oriented loop strip; rebind this finger only.')
    tree, mapping = _tree(bm), {}
    row_ids = {i for row in old_rows[1:-1] for i in row}
    for vi, point in coordinates.items():
        hits = tree.find_range(point, 1e-6)
        if len(hits) == 1: mapping[vi] = hits[0][1]
        elif vi not in row_ids:
            raise ValueError('The finger cap/root/reference boundary changed; rebind this finger only.')
    for face_id, ids in old_faces.items():
        if face_id in old_strip: continue
        if any(i not in mapping for i in ids) or _cycle([mapping[i] for i in ids]) not in new_faces:
            raise ValueError('The finger cap/root/reference boundary changed; rebind this finger only.')

    revised = copy.deepcopy(record)
    provenance = {}
    for row, (band, fraction) in zip(new_rows, samples):
        for column, vi in enumerate(row):
            provenance[vi] = old_rows[band][column], old_rows[band+1][column], fraction
    for variant in ('basis', 'current'):
        old_values = {i: Vector(co) for i, co in record[variant]['coordinates']}
        values = {ni: old_values[i] for i, ni in mapping.items() if i in old_values}
        for vi, (a, b, fraction) in provenance.items():
            if a not in old_values or b not in old_values:
                raise ValueError('The saved Shape Key reference is incomplete; rebind this finger only.')
            values[vi] = old_values[a].lerp(old_values[b], fraction)
        revised[variant]['coordinates'] = [[i, list(co)] for i, co in sorted(values.items())]
    body = finger_internal.prepare_body(bm, candidate, record['basis']['path'])
    revised['body'] = body
    revised['input'] = {'kind': 'VERTS', 'ids': [i for i, _ in revised['basis']['coordinates']]}
    revised['support_input'] = {'kind': 'FACES', 'ids': finger_internal.support_faces(body)}
    revised.pop('start_input', None)
    revised['topology'] = definition._topology(bm)
    # Keep the artist's spatial endpoints, normal, centering and internal path.
    # Verification is a proof of the existing path, not a new solve/refit.
    finger_internal.verify(bm, body, revised['internal'])
    return enrich(revised, bm)


def _reflect_evidence(record, reflection):
    """Reflect saved spatial evidence without pretending its indices are current."""
    result = copy.deepcopy(record)
    mirror = reflection.inverted() @ Matrix.Diagonal((-1, 1, 1, 1)) @ reflection
    normal_matrix = mirror.to_3x3().inverted().transposed()
    for variant in ('basis', 'current'):
        sample = result[variant]
        sample['coordinates'] = [[i, list(detect.reflect(co, reflection))] for i, co in sample['coordinates']]
        sample['path'] = [list(detect.reflect(p, reflection)) for p in sample['path']]
        if sample.get('normal'): sample['normal'] = list((normal_matrix @ Vector(sample['normal'])).normalized())
    for part in ('internal', 'surface'):
        if part in result:
            result[part]['path'] = [list(detect.reflect(p, reflection)) for p in result[part]['path']]
    centering = result.get('surface', {}).get('centering')
    if centering:
        centering['path'] = [list(detect.reflect(p, reflection)) for p in centering['path']]
        centering['normal'] = list((normal_matrix @ Vector(centering['normal'])).normalized())
    if result.get('body'):
        for name in ('root', 'tip'): result['body'][name] = list(detect.reflect(result['body'][name], reflection))
    for indices in result.get('local_evidence', {}).get('FACES', {}).values(): indices.reverse()
    return result


def remap_record(record, bm, *, reflection=None, target=None, allow_ring_edits=False, candidate=None):
    try:
        return _remap_record_exact(record, bm, reflection=reflection, target=target)
    except ValueError:
        if not allow_ring_edits: raise
        if reflection is not None:
            if target is None: raise
            return _remap_ring_edits(_reflect_evidence(record, reflection), bm, target)
        return _remap_ring_edits(record, bm, candidate)


def _remap_bend_evidence(record, before, after, bm):
    """Keep an accepted local Bend direction on a proven unchanged sleeve.

    A captured face may have been subdivided or dissolved. Its spatial normal
    remains valid only when all its old evidence belongs to the sleeve whose
    entire surface has already passed the bidirectional geometry proof.
    """
    if not before.get('body') or not after.get('body') or any(
            record[name] != before[name] for name in ('basis_key', 'key')):
        raise ValueError('This Bend reference needs recapture on this finger only.')
    for variant in ('basis', 'current'):
        values = {i: Vector(co) for i, co in before[variant]['coordinates']}
        if any(i not in values or (Vector(co)-values[i]).length > 1e-6
               for i, co in record[variant]['coordinates']):
            raise ValueError('The Bend reference is outside the verified finger sleeve.')
    evidence = record.get('local_evidence')
    if evidence is None or evidence.get('EDGES'):
        raise ValueError('This Bend reference has no verified face evidence.')
    faces = {_cycle(ids) for ids in before['local_evidence']['FACES'].values()}
    if not evidence.get('FACES') or any(_cycle(ids) not in faces for ids in evidence['FACES'].values()):
        raise ValueError('The Bend reference is outside the verified finger sleeve.')
    result = copy.deepcopy(record)
    for variant in ('basis', 'current'):
        result[variant]['coordinates'] = copy.deepcopy(after[variant]['coordinates'])
    result['input'] = copy.deepcopy(after['input'])
    result['support_input'] = copy.deepcopy(after['support_input'])
    result.pop('start_input', None)
    result['topology'] = after['topology']
    return enrich(result, bm)


def validate_local(obj, record, basis, bm, *, reader=None):
    # The remap always compares Basis coordinates, then verifies the requested
    # key independently. Editing another finger never invalidates this by itself.
    base = reader.snapshot(obj, record['basis_key']) if reader else definition._snapshot(obj, record['basis_key'])
    try: remapped = remap_record(record, base, allow_ring_edits=True)
    finally:
        if reader is None: base.free()
    variant = 'basis' if basis else 'current'
    if any((bm.verts[i].co-Vector(co)).length > 1e-6 for i, co in remapped[variant]['coordinates']):
        raise ValueError('This finger reference changed; recapture this finger only.')
    return remapped


def _mirrored_ring_surface(bm, source, target, to_plane):
    """Same oriented mirrored surface despite different complete ring counts."""
    first, second = source['rings'], target['rings']
    width = len(first[0])
    if any(len(row) != width for row in first+second):
        raise ValueError('Opposite finger circumference differs.')
    tolerance = max(source['length']*1e-4, 1e-7)
    columns = []
    for vi in first[0]:
        point = detect.reflect(bm.verts[vi].co, to_plane)
        hits = [j for j, ni in enumerate(second[0]) if (point-bm.verts[ni].co).length <= tolerance]
        if len(hits) != 1: raise ValueError('Opposite finger root geometry is not symmetric.')
        columns.append(hits[0])
    if len(set(columns)) != width: raise ValueError('Opposite root correspondence is ambiguous.')
    second = [[row[column] for column in columns] for row in second]
    a = [[detect.reflect(bm.verts[i].co, to_plane) for i in row] for row in first]
    b = [[bm.verts[i].co.copy() for i in row] for row in second]
    if any((p-q).length > tolerance for p, q in zip(a[-1], b[-1])):
        raise ValueError('Opposite fingertip boundary geometry is not symmetric.')
    for rows, reference in ((a, b), (b, a)):
        try:
            positions = [sum(_row_sample(row, reference, tolerance)) for row in rows]
        except ValueError as exc:
            # This compares two CURRENT surfaces, not a saved reference with
            # an edited surface. Re-capturing the valid side cannot repair it.
            raise ValueError(PAIR_SYNC_WARNING) from exc
        if any(z-x <= 1e-6 for x, z in zip(positions, positions[1:])):
            raise ValueError('Opposite loops cross or duplicate a section.')
    def strip_faces(rows):
        return {frozenset((r[j], s[j], s[(j+1) % width], r[(j+1) % width]))
                for r, s in zip(rows, rows[1:]) for j in range(width)}
    source_strip, target_strip = strip_faces(first), strip_faces(second)
    source_caps = [bm.faces[i] for i in source['faces'] if frozenset(v.index for v in bm.faces[i].verts) not in source_strip]
    target_caps = [bm.faces[i] for i in target['faces'] if frozenset(v.index for v in bm.faces[i].verts) not in target_strip]
    tree = KDTree(len(target['vertices']))
    for vi in target['vertices']: tree.insert(bm.verts[vi].co, vi)
    tree.balance()
    mapping = {}
    for face in source_caps:
        for vertex in face.verts:
            hits = tree.find_range(detect.reflect(vertex.co, to_plane), tolerance)
            if len(hits) != 1: raise ValueError('Opposite fingertip cap geometry is not symmetric.')
            mapping[vertex.index] = hits[0][1]
    if {_cycle(list(reversed([mapping[v.index] for v in face.verts]))) for face in source_caps} != {
            _cycle(v.index for v in face.verts) for face in target_caps}:
        raise ValueError('Opposite cap connections / orientation differ.')
    # The strip itself must retain the same winding after reflection.
    source_lookup = {frozenset(v.index for v in bm.faces[i].verts): bm.faces[i] for i in source['faces']}
    target_lookup = {_cycle(v.index for v in bm.faces[i].verts) for i in target['faces']}
    seed = [first[0][0], first[1][0], first[1][1], first[0][1]]
    face = source_lookup.get(frozenset(seed))
    if face is None: raise ValueError('The source finger strip is incomplete.')
    forward = _cycle(v.index for v in face.verts) == _cycle(seed)
    for r, s in zip(first, first[1:]):
        for j in range(width):
            polygon = [r[j], s[j], s[(j+1) % width], r[(j+1) % width]]
            face = source_lookup.get(frozenset(polygon))
            if face is None or (_cycle(v.index for v in face.verts) == _cycle(polygon)) != forward:
                raise ValueError('The source finger strip orientation is inconsistent.')
    for r, s in zip(second, second[1:]):
        for j in range(width):
            polygon = [r[j], s[j], s[(j+1) % width], r[(j+1) % width]]
            if forward: polygon.reverse()
            if _cycle(polygon) not in target_lookup:
                raise ValueError('Opposite strip connections / orientation differ.')


def _warnings(bm, candidates, to_plane):
    warnings = {}
    for digit in detect.DIGITS:
        left, right = (candidates.get(f'{digit}.{side}') for side in ('L', 'R'))
        if not left or not right:
            warnings[digit] = 'Opposite finger / closed tip not found.'
            continue
        try: detect.vertex_map(bm, left, right, to_plane)
        except ValueError:
            try: _mirrored_ring_surface(bm, left, right, to_plane)
            except ValueError: warnings[digit] = PAIR_SYNC_WARNING
    return warnings


def _match_previous(old, candidates):
    result, used = {}, set()
    for key, source in old.items():
        options = sorted(((Vector(c['root'])-Vector(source['root'])).length+
                          (Vector(c['tip'])-Vector(source['tip'])).length, i, c)
                         for i, c in enumerate(candidates))
        if not options: continue
        distance, i, c = options[0]
        if i in used or distance > source['length']*.3: continue
        if len(options) > 1 and options[1][0] < distance*2+source['radius']*.25: continue
        result[key] = c
        used.add(i)
    return result


def survey(context, obj, bm, selected=None, *, force=False):
    from . import finger_range
    bank = obj.character_designer_finger_bank
    signature = stamp(bm, obj)
    old = json.loads(bank.survey) if bank.survey else {}
    if not force and old.get('stamp') == signature:
        if old.get('pair_warning_version') != PAIR_WARNING_VERSION:
            # Refresh older mislabelled pair diagnostics without repeating
            # detection or changing any saved finger reference.
            old['warnings'] = _warnings(bm, old['candidates'], plane(obj))
            old['pair_warning_version'] = PAIR_WARNING_VERSION
        return old
    if old:
        # Retain missing descriptors so a later Recheck can recover them.
        anchors = dict(old.get('anchors', old['candidates']))
        candidates = _match_previous(anchors, detect.census(bm, finger_range.quad_band))
    else:
        if not selected: raise ValueError('Select faces or an internal loop on one finger, then Capture Detection.')
        candidates = detect.detect(bm, finger_range.quad_band, selected, plane(obj))
        anchors = dict(candidates)
        for key, c in list(candidates.items()):
            mate = key[:-1]+('R' if key[-1] == 'L' else 'L')
            if mate not in anchors:
                anchors[mate] = dict(c, root=list(detect.reflect(c['root'], plane(obj))),
                                     tip=list(detect.reflect(c['tip'], plane(obj))))
    anchors.update(candidates)
    return {'stamp': signature, 'candidates': candidates, 'anchors': anchors,
            'pair_warning_version': PAIR_WARNING_VERSION,
            'warnings': _warnings(bm, candidates, plane(obj))}


def _commit_survey(context, obj, report):
    bank = obj.character_designer_finger_bank
    bank.survey = json.dumps(report)
    bank.needs_recheck = ''
    for digit in detect.DIGITS:
        for side in ('L', 'R'): _slot(bank, f'{digit}.{side}')
    context.scene.character_designer_finger_setup = obj


def _resolve(report, spec, bm):
    keys = list(report['candidates'])
    vertices = [v.index for v in definition._input_vertices(bm, spec)]
    index = detect.owner([report['candidates'][k] for k in keys], vertices)
    return keys[index]


def _mirror(context, obj, key, bm, report):
    bank = obj.character_designer_finger_bank
    slot = _slot(bank, key)
    mate_key = key[:-1]+('R' if key[-1] == 'L' else 'L')
    mate = _slot(bank, mate_key)
    warning = report['warnings'].get(key.split('.')[0])
    if warning:
        mate.error = warning
        return
    source, target = report['candidates'][key], report['candidates'][mate_key]
    try:
        try: detect.vertex_map(bm, source, target, plane(obj))
        except ValueError: _mirrored_ring_surface(bm, source, target, plane(obj))
        original = json.loads(slot.guide.record)
        record = remap_record(original, bm, reflection=plane(obj), target=target, allow_ring_edits=True)
        record['bank_key'] = mate_key
        definition._validate_mesh(obj, record, slot.guide.use_basis)
        if record.get('internal'):
            from . import finger_internal
            record['internal'].update(finger_internal.verify(bm, record['body'], record['internal']))
        if mate.guide.record:
            previous = json.loads(mate.guide.record)
            origin = previous.get('capture_source', previous.get('surface', {}).get('origin', {}).get('bank_key'))
            old_normal, new_normal = previous['basis'].get('normal'), record['basis'].get('normal')
            if origin == mate_key and old_normal and new_normal:
                old_bend = Vector(old_normal)*(1 if mate.guide.flip_bend else -1)
                new_bend = Vector(new_normal)*(1 if slot.guide.flip_bend else -1)
                if old_bend.normalized().dot(new_bend.normalized()) < .98:
                    raise ValueError('Independently captured bend directions conflict; the opposite definition was retained. Review the top strips.')
        top = None
        if slot.guide.bend_record:
            captured_top = json.loads(slot.guide.bend_record)
            try: top = remap_record(captured_top, bm, reflection=plane(obj), target=target)
            except ValueError:
                top = _remap_bend_evidence(_reflect_evidence(captured_top, plane(obj)),
                                           _reflect_evidence(original, plane(obj)), record, bm)
        # Stage everything before touching the previous opposite guide. A
        # programmatic flip assignment is not a user Reverse Bend action.
        from .finger_definition_ui import reference_write
        with reference_write():
            for name in FIELDS: setattr(mate.guide, name, getattr(slot.guide, name))
            mate.guide.record = json.dumps(record)
            if top: mate.guide.bend_record = json.dumps(top)
            mate.guide.pending_source, mate.guide.pending = None, ''
            mate.guide.revision = slot.guide.revision+'-mate'
            mate.guide.confirmed = slot.guide.confirmed
        mate.bones = ''
        mate.error = ''
    except ValueError as exc:
        mate.error = str(exc)


def _internal_record(obj, bm, spec, candidate, key):
    """One rest reference, regardless of the active Shape Key; selection kept."""
    from . import finger_internal
    vertices = definition._input_vertices(bm, spec)
    if len(definition._components(bm, spec)) != 1:
        raise ValueError('Select one connected strip, edge path or loop on this finger.')
    main = (Vector(candidate['tip'])-Vector(candidate['root'])).normalized()
    projected = [v.co.dot(main) for v in vertices]
    closed = False
    if spec['kind'] == 'EDGES':
        edges = {bm.edges[i] for i in spec['ids']}
        closed = all(sum(e in edges for e in v.link_edges) == 2 for v in vertices)
    marker = closed or max(projected)-min(projected) < candidate['radius']
    try: sample = definition._sample(bm, spec, path=not marker)
    except ValueError as exc:
        raise ValueError('The selected strip/edge path has no reliable longitudinal range: '+str(exc)) from exc
    surface = {'path': copy.deepcopy(sample['path']), 'range_mode': 'DETECTED' if marker else 'SELECTED',
               'origin': {'input': copy.deepcopy(spec), 'topology': definition._topology(bm),
                          'coordinates': copy.deepcopy(sample['coordinates']), 'bank_key': key}}
    if marker:
        sample['path'] = [list(sum((bm.verts[i].co for i in row), Vector())/len(row)) for row in candidate['rings']]
        if (Vector(candidate['tip'])-Vector(sample['path'][-1])).length > 1e-7:
            sample['path'].append(candidate['tip'])
    elif (Vector(sample['path'][-1])-Vector(sample['path'][0])).dot(main) < 0:
        sample['path'].reverse()
        surface['path'].reverse()
    # A strip may wrap slightly around the closed fingertip. Its extremum,
    # not its traversal's final point, is the distal range boundary. Preserve
    # the complete original surface path independently.
    zs = [Vector(p).dot(main) for p in sample['path']]
    furthest = max(range(len(zs)), key=zs.__getitem__)
    if (furthest < len(zs)-1 and zs[furthest] >= Vector(candidate['tip']).dot(main)-candidate['radius']
            and min(zs[furthest:]) > zs[furthest]-candidate['radius']*.5):
        sample['path'] = sample['path'][:furthest+1]
    if any((Vector(b)-Vector(a)).dot(main) < -candidate['length']*1e-4 for a, b in zip(sample['path'], sample['path'][1:])):
        raise ValueError('The selected path folds back along the finger; a root-to-tip range is not reliable.')
    if sample['normal'] is None and spec['kind'] == 'EDGES' and not closed:
        adjacent = {f.index for i in spec['ids'] for f in bm.edges[i].link_faces if f.index in candidate['faces']}
        sample['normal'] = definition._normal(bm, {'kind': 'FACES', 'ids': sorted(adjacent)}) if adjacent else None
    body = finger_internal.prepare_body(bm, candidate, sample['path'])
    centering = None if marker else finger_internal.surface_centering(bm, body, spec)
    if centering: surface['centering'] = centering
    if centering and spec['kind'] == 'FACES':
        # Stable longitudinal sections, excluding the wrapped cap/root fan,
        # provide the top normal from this same capture (never another finger).
        stable = set(candidate['vertices'])
        faces_for_normal = [bm.faces[i] for i in spec['ids']
                            if len(bm.faces[i].verts) == 4 and all(v.index in stable for v in bm.faces[i].verts)]
        normal = Vector(centering['normal'])
        if not faces_for_normal or any(f.normal.dot(normal) < .2 for f in faces_for_normal
                                      if abs(f.normal.dot(main)) < .8):
            raise ValueError('The selected top strip has conflicting surface normals; keep a consistent longitudinal side.')
        sample['normal'] = list(normal)
    if spec['kind'] == 'FACES' and not marker and sample['normal'] is None:
        raise ValueError('The longitudinal surface has no consistent top normal. Select one continuous side; the previous definition was retained.')
    faces = finger_internal.support_faces(body)
    support = sorted({v.index for i in faces for v in bm.faces[i].verts} | {v.index for v in vertices})
    sample['coordinates'] = [[i, list(bm.verts[i].co)] for i in support]
    sample['label'] = 'Internal straight axis'
    record = {'kind': 'MESH', 'input': copy.deepcopy(spec), 'support_input': {'kind': 'FACES', 'ids': faces},
              'topology': definition._topology(bm), 'key': definition._basis_name(obj), 'basis_key': definition._basis_name(obj),
              'basis': sample, 'current': copy.deepcopy(sample), 'direction': 'Detected tip', 'bank_key': key, 'capture_source': key,
              'surface': surface, 'body': body, 'internal': finger_internal.solve(bm, body, sample['path'], centering)}
    definition._validate_mesh(obj, record, True)
    return enrich(record, bm)


def capture(context, action='CAPTURE'):
    obj = context.edit_object
    if context.mode != 'EDIT_MESH' or not obj or len(context.objects_in_mode_unique_data) != 1:
        raise ValueError('Use Mesh Edit Mode for automatic five-finger detection.')
    current = definition._snapshot(obj, definition._key_name(obj))
    base = definition._snapshot(obj, definition._basis_name(obj))
    try:
        spec = definition._selection(context, current)
        report = survey(context, obj, base, [v.index for v in definition._input_vertices(base, spec)])
        key = _resolve(report, spec, base)
        bank = obj.character_designer_finger_bank
        old_active, old_obj = bank.active, context.scene.character_designer_finger_setup
        slot = bank.slots.get(key)
        if action == 'CAPTURE':
            record = _internal_record(obj, base, spec, report['candidates'][key], key)
            if definition._key_name(obj) != definition._basis_name(obj):
                from . import finger_internal
                try: finger_internal.verify(current, record['body'], record['internal'])
                except ValueError as exc:
                    raise ValueError('The visible finger deformation cannot safely contain the rest reference axis. No definition or Shape Key was changed.') from exc
            # No data/old reference changes until geometry and the entire axis
            # have passed. Shape Key values, active key and scene objects stay put.
            _commit_survey(context, obj, report)
            slot = _slot(bank, key)
            guide = slot.guide
            from .finger_definition_ui import reference_write
            with reference_write():
                guide.source, guide.record = obj, json.dumps(record)
                guide.revision, guide.use_basis = uuid.uuid4().hex, True
                guide.bend_source, guide.bend_record, guide.flip_bend = None, '', False
                guide.pending_source, guide.pending = None, ''
                guide.confirmed, guide.status, slot.error = True, '', ''
            slot.bones = ''
            bank.active, bank.status = key, ''
            select(context, key.split('.')[0], key[-1])
            return key
        if action == 'END' and (not slot or not slot.guide.pending):
            raise ValueError(f'Mark Start on {detect.LABELS[key.split(".")[0]]} {key[-1]} first. Start / End cannot cross fingers.')
        # Capture to a temporary legacy state for atomic failure behavior.
        scratch = context.scene.character_designer_finger_definition
        saved = {name: getattr(scratch, name) for name in FIELDS}
        try:
            for name in FIELDS:
                value = getattr(slot.guide, name) if slot else (None if name in {'source', 'bend_source', 'pending_source'}
                        else True if name == 'use_basis' else False if name in {'confirmed', 'flip_bend'} else '')
                setattr(scratch, name, value)
            bank.active = ''
            context.scene.character_designer_finger_setup = None
            definition.mark(context, end=action == 'END')
            field = 'pending' if action == 'START' else 'record'
            record = enrich(json.loads(getattr(scratch, field)), base)
            record['bank_key'] = key
            setattr(scratch, field, json.dumps(record))
            staged = {name: getattr(scratch, name) for name in FIELDS}
        finally:
            for name, value in saved.items(): setattr(scratch, name, value)
            bank.active = old_active
            context.scene.character_designer_finger_setup = old_obj
        _commit_survey(context, obj, report)
        slot = _slot(bank, key)
        from .finger_definition_ui import reference_write
        with reference_write():
            for name, value in staged.items(): setattr(slot.guide, name, value)
        slot.error = ''
        bank.active = key
        return key
    finally:
        current.free()
        base.free()


def selected_digits(bank):
    """Display selection is separate from the one active editing identity."""
    chosen = set(bank.visible_digits) if bank.selection_initialized else {bank.active.split('.')[0]}
    return tuple(d for d in detect.DIGITS if d in chosen)


def display_side(bank):
    """The captured/active side owns the setup and viewport guides; default L."""
    return 'R' if bank.active.endswith('.R') else 'L'


def display_keys(bank):
    return tuple(d+'.'+display_side(bank) for d in selected_digits(bank))


def switch_side(context):
    obj = active_object(context)
    if not obj or not obj.character_designer_finger_bank.active:
        raise ValueError('Capture a finger on this mesh first.')
    state = obj.character_designer_finger_bank
    # Do not reset a multi-selection (or resurrect an intentionally empty one).
    state.active = state.active.split('.')[0]+('.L' if display_side(state) == 'R' else '.R')


def select(context, digit, side=None, *, mode='SINGLE'):
    obj = active_object(context)
    if not obj: raise ValueError('Capture a finger on this mesh first.')
    bank = obj.character_designer_finger_bank
    if digit not in detect.DIGITS or mode not in {'CLICK', 'SINGLE', 'TOGGLE', 'RANGE'}:
        raise ValueError('Unknown finger display selection.')
    side = side or (bank.active[-1] if bank.active else 'L')
    chosen = set(selected_digits(bank))
    if mode == 'CLICK':
        # A visible button can always be switched off, including the last one.
        # Internal callers retain SINGLE's explicit focus semantics (Capture).
        if digit in chosen: chosen.remove(digit)
        else: chosen = {digit}
        bank.selection_anchor = digit
    elif mode == 'SINGLE':
        chosen, bank.selection_anchor = {digit}, digit
    elif mode == 'TOGGLE':
        chosen.symmetric_difference_update({digit})
        bank.selection_anchor = digit
    else:
        anchor = bank.selection_anchor if bank.selection_anchor in detect.DIGITS else digit
        a, b = sorted((detect.DIGITS.index(anchor), detect.DIGITS.index(digit)))
        chosen.update(detect.DIGITS[a:b+1])
        bank.selection_anchor = anchor
    bank.visible_digits, bank.selection_initialized = chosen, True
    if digit in chosen: bank.active = f'{digit}.{side}'
    elif chosen and bank.active.split('.')[0] not in chosen:
        closest = min(chosen, key=lambda d: (abs(detect.DIGITS.index(d)-detect.DIGITS.index(digit)), detect.DIGITS.index(d)))
        bank.active = f'{closest}.{side}'


def sync(context):
    """Commit explicit active-side settings without generating a mate reference."""
    obj = active_object(context)
    if not obj: return
    bank = obj.character_designer_finger_bank
    if not bank.active or not definition.state(context).record: return
    base = definition._snapshot(obj, definition._basis_name(obj))
    try:
        report = survey(context, obj, base)
        _commit_survey(context, obj, report)
        guide = definition.state(context)
        if guide.bend_record: guide.bend_record = json.dumps(enrich(json.loads(guide.bend_record), base))
    finally: base.free()


def recheck(context, *, all_slots=False):
    """Explicitly recheck the active saved side; other references stay untouched."""
    obj = active_object(context)
    if not obj: raise ValueError('Capture a finger first.')
    bank = obj.character_designer_finger_bank
    base = definition._snapshot(obj, definition._basis_name(obj))
    try:
        report = survey(context, obj, base, force=True)
        changes, outcome = [], {'adapted': [], 'unchanged': [], 'failed': {}, 'survey': report}
        selected = tuple(bank.slots) if all_slots else (bank.slots.get(bank.active),)
        for slot in selected:
            if slot is None: continue
            guide, updates, adapted = slot.guide, {}, False
            if not configured(slot):
                # Empty slots are detection candidates, not failed captures.
                changes.append((slot, {}, ''))
                continue
            try:
                if slot.name not in report['candidates']:
                    raise ValueError('Finger body / closed tip not found; saved settings retained.')
                for field in ('record', 'bend_record', 'pending'):
                    text = getattr(guide, field)
                    if not text: continue
                    record = json.loads(text)
                    if field == 'bend_record' and guide.bend_source and guide.bend_source != obj:
                        definition._validate_mesh(guide.bend_source, record, guide.use_basis)
                        updates[field] = text
                        continue
                    previous_points = {tuple(co) for _, co in record['basis']['coordinates']}
                    try:
                        record = remap_record(record, base, allow_ring_edits=field != 'pending',
                                              candidate=report['candidates'][slot.name])
                    except ValueError:
                        if field != 'bend_record' or 'record' not in updates: raise
                        record = _remap_bend_evidence(record, json.loads(guide.record),
                                                      json.loads(updates['record']), base)
                    adapted = adapted or previous_points != {tuple(co) for _, co in record['basis']['coordinates']}
                    updates[field] = json.dumps(record)
                changes.append((slot, updates, ''))
                outcome['adapted' if adapted else 'unchanged'].append(slot.name)
            except ValueError as exc:
                changes.append((slot, {}, str(exc)))
                outcome['failed'][slot.name] = str(exc)
        _commit_survey(context, obj, report)
        for slot, updates, error in changes:
            for field, value in updates.items(): setattr(slot.guide, field, value)
            slot.error = error
        # A completed recheck has current per-slot results. A stale transaction
        # failure must no longer label every valid reference as a previous result.
        if bank.status.startswith('Update failed; previous result retained:'):
            bank.status = ''
        return outcome
    finally: base.free()


def adapt_topology(context):
    """Explicit all-slot remap; never call from background/panel/draw callbacks.

    No geometry, weights, Shape Keys, bones, confirmation or revision is written.
    Failures retain that slot's saved reference and cannot invalidate other digits.
    """
    return recheck(context, all_slots=True)


def dirty(context, *, reader=None):
    obj = active_object(context)
    if not obj or not obj.character_designer_finger_bank.survey: return False
    bm = reader.snapshot(obj, definition._basis_name(obj)) if reader else definition._snapshot(obj, definition._basis_name(obj))
    try: return stamp(bm, obj, reader=reader) != json.loads(obj.character_designer_finger_bank.survey)['stamp']
    finally:
        if reader is None: bm.free()


def validate_top(context):
    obj = active_object(context)
    if not obj: return
    bm = definition._snapshot(obj, definition._basis_name(obj))
    try:
        report = survey(context, obj, bm)
        key = _resolve(report, definition._selection(context, bm), bm)
        if key != obj.character_designer_finger_bank.active:
            raise ValueError('The top surface belongs to another finger. Capture that finger first.')
    finally: bm.free()




def current_candidate(context):
    obj = active_object(context)
    if not obj: return None
    if dirty(context): recheck(context)
    b = obj.character_designer_finger_bank
    slot = b.slots.get(b.active)
    if not slot or slot.error: raise ValueError(slot.error if slot else 'Capture this finger first.')
    candidate = json.loads(b.survey)['candidates'].get(b.active)
    if not candidate: raise ValueError('This finger body needs a topology recheck.')
    return candidate
