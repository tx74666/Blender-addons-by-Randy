"""Five paired finger definitions, owned by the mesh, not capture order.

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


def stamp(bm, obj):
    return hashlib.sha256((definition._topology(bm)+repr([tuple(v.co) for v in bm.verts])+
                           repr(tuple(tuple(r) for r in plane(obj)))).encode()).hexdigest()


def _slot(bank, key):
    slot = bank.slots.get(key)
    if slot is None:
        slot = bank.slots.add()
        slot.name = key
    return slot


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


def remap_record(record, bm, *, reflection=None, target=None):
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


def validate_local(obj, record, basis, bm):
    # The remap always compares Basis coordinates, then verifies the requested
    # key independently. Editing another finger never invalidates this by itself.
    base = definition._snapshot(obj, record['basis_key'])
    try: remapped = remap_record(record, base)
    finally: base.free()
    variant = 'basis' if basis else 'current'
    if any((bm.verts[i].co-Vector(co)).length > 1e-6 for i, co in remapped[variant]['coordinates']):
        raise ValueError('This finger reference changed; recapture this finger only.')


def _warnings(bm, candidates, to_plane):
    warnings = {}
    for digit in detect.DIGITS:
        left, right = (candidates.get(f'{digit}.{side}') for side in ('L', 'R'))
        if not left or not right:
            warnings[digit] = 'Opposite finger / closed tip not found.'
            continue
        try: detect.vertex_map(bm, left, right, to_plane)
        except ValueError as exc: warnings[digit] = str(exc)
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
    from . import finger_layout
    bank = obj.character_designer_finger_bank
    signature = stamp(bm, obj)
    old = json.loads(bank.survey) if bank.survey else {}
    if not force and old.get('stamp') == signature: return old
    if old:
        # Retain missing descriptors so a later Recheck can recover them.
        anchors = dict(old.get('anchors', old['candidates']))
        candidates = _match_previous(anchors, detect.census(bm, finger_layout._quad_band))
    else:
        if not selected: raise ValueError('Select faces or an internal loop on one finger, then Capture Detection.')
        candidates = detect.detect(bm, finger_layout._quad_band, selected, plane(obj))
        anchors = dict(candidates)
        for key, c in list(candidates.items()):
            mate = key[:-1]+('R' if key[-1] == 'L' else 'L')
            if mate not in anchors:
                anchors[mate] = dict(c, root=list(detect.reflect(c['root'], plane(obj))),
                                     tip=list(detect.reflect(c['tip'], plane(obj))))
    anchors.update(candidates)
    return {'stamp': signature, 'candidates': candidates, 'anchors': anchors,
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
        detect.vertex_map(bm, source, target, plane(obj))
        record = remap_record(json.loads(slot.guide.record), bm, reflection=plane(obj), target=target)
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
        top = remap_record(json.loads(slot.guide.bend_record), bm, reflection=plane(obj), target=target) if slot.guide.bend_record else None
        # Stage everything before touching the previous opposite guide.
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
            guide.source, guide.record = obj, json.dumps(record)
            guide.revision, guide.use_basis = uuid.uuid4().hex, True
            guide.bend_source, guide.bend_record, guide.flip_bend = None, '', False
            guide.pending_source, guide.pending = None, ''
            guide.confirmed, guide.status, slot.error = True, '', ''
            slot.bones = ''
            bank.active, bank.status = key, ''
            _mirror(context, obj, key, base, report)
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
        for name, value in staged.items(): setattr(slot.guide, name, value)
        slot.error = ''
        bank.active = key
        if action != 'START': _mirror(context, obj, key, base, report)
        return key
    finally:
        current.free()
        base.free()


def selected_digits(bank):
    """Display selection is separate from the one active editing identity."""
    chosen = set(bank.visible_digits) if bank.selection_initialized else {bank.active.split('.')[0]}
    return tuple(d for d in detect.DIGITS if d in chosen)


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
        _mirror(context, obj, bank.active, base, report)
    finally: base.free()


def recheck(context, *, own_layout=False, updated_keys=None):
    obj = active_object(context)
    if not obj: raise ValueError('Capture a finger first.')
    bank = obj.character_designer_finger_bank
    base = definition._snapshot(obj, definition._basis_name(obj))
    try:
        report = survey(context, obj, base, force=True)
        changes = []
        for slot in bank.slots:
            guide, updates = slot.guide, {}
            try:
                if slot.name not in report['candidates']:
                    raise ValueError('Finger body / closed tip not found; saved settings retained.')
                for field in ('record', 'bend_record', 'pending'):
                    text = getattr(guide, field)
                    if not text: continue
                    record = json.loads(text)
                    if own_layout and slot.name in (updated_keys or {bank.active}) and field != 'pending':
                        # The verified layout preserved the original reference
                        # span. Restamp against the newly generated local body.
                        c = report['candidates'][slot.name]
                        record['input'] = {'kind': 'VERTS', 'ids': c['vertices']}
                        record['support_input'] = {'kind': 'FACES', 'ids': c['faces']}
                        record.pop('start_input', None)
                        for variant in ('basis', 'current'):
                            probe = definition._snapshot(obj, record['basis_key'] if variant == 'basis' else record['key'])
                            try: record[variant]['coordinates'] = [[i, list(probe.verts[i].co)] for i in c['vertices']]
                            finally: probe.free()
                        record['topology'] = definition._topology(base)
                        if record.get('internal'):
                            from . import finger_internal
                            body = finger_internal.prepare_body(base, c, record['basis']['path'])
                            record['body'] = body
                            faces = finger_internal.support_faces(body)
                            record['support_input'] = {'kind': 'FACES', 'ids': faces}
                            vertices = sorted({v.index for i in faces for v in base.faces[i].verts})
                            for variant in ('basis', 'current'):
                                probe = definition._snapshot(obj, record['basis_key'] if variant == 'basis' else record['key'])
                                try: record[variant]['coordinates'] = [[i, list(probe.verts[i].co)] for i in vertices]
                                finally: probe.free()
                            record['internal'] = finger_internal.solve(base, body, record['basis']['path'], record.get('surface', {}).get('centering'))
                        record = enrich(record, base)
                    else:
                        record = remap_record(record, base)
                    updates[field] = json.dumps(record)
                changes.append((slot, updates, ''))
            except ValueError as exc: changes.append((slot, {}, str(exc)))
        _commit_survey(context, obj, report)
        for slot, updates, error in changes:
            for field, value in updates.items(): setattr(slot.guide, field, value)
            slot.error = error
        # Retry missing opposite definitions only when the source is valid;
        # do not replace existing independent adjustments on a plain Recheck.
        for digit in detect.DIGITS:
            a, b = (_slot(bank, f'{digit}.{s}') for s in ('L', 'R'))
            if a.guide.record and not a.error and not b.guide.record: _mirror(context, obj, a.name, base, report)
            elif b.guide.record and not b.error and not a.guide.record: _mirror(context, obj, b.name, base, report)
    finally: base.free()


def dirty(context):
    obj = active_object(context)
    if not obj or not obj.character_designer_finger_bank.survey: return False
    bm = definition._snapshot(obj, definition._basis_name(obj))
    try: return stamp(bm, obj) != json.loads(obj.character_designer_finger_bank.survey)['stamp']
    finally: bm.free()


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


def after_layout(context):
    obj = active_object(context)
    if obj is None: return
    try: recheck(context, own_layout=True)
    except ValueError as exc:
        obj.character_designer_finger_bank.needs_recheck = 'Layout updated; recheck references: '+str(exc)


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
