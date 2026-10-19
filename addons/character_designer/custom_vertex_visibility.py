"""One reversible Edit Mode visibility overlay for independent mesh catalogs.

Membership and visibility metadata live in native BMesh custom data so native
Edit Mode Undo owns them together.  There are no observers or deferred writes.
Manual H/Alt-H changes are reconciled only at the next explicit operation.

Custom_ selections and Part_ selections have independent membership catalogs.
Existing Custom_ visibility requests remain supported by the legacy Python API;
new Parts use the same .cdvg_* marker format, keyed by the complete native name.
Both catalogs share one baseline so overlapping requests cannot mistake another
overlay for a manual hide. Nothing is migrated or restored on import. Parts and
legacy requests have separate explicit restore operations; clearing the final
request restores the manual baseline. The old global reset API is retained.
Membership edits refresh only when their own catalog has visibility requests.
"""
import hashlib

import bmesh


HIDDEN_PREFIX = '.cdvg_hidden_'
SOLO_PREFIX = '.cdvg_solo_'
STATE_PREFIX = '.cdvg_visibility_'
PART_PREFIX = 'Part_'
STATE_NAMES = {
    'POINT': STATE_PREFIX + 'vertex_state',
    'EDGE': STATE_PREFIX + 'edge_state',
    'FACE': STATE_PREFIX + 'face_state',
}
_BASELINE = 1
_LAST_APPLIED = 2
_CAPTURED = 4
_VALID_BITS = _BASELINE | _LAST_APPLIED | _CAPTURED
_LAYER_KINDS = ('int', 'float', 'float_vector', 'string', 'bool',
                'color', 'float_color', 'byte_color', 'uv', 'shape')


def _groups():
    # The selection module calls this module from its explicit operators.
    from . import custom_vertex_groups
    return custom_vertex_groups


def _is_part(attribute):
    # Keep visibility validation independent of the Parts UI module and its
    # registration lifecycle. This is the same native catalog contract.
    return (attribute is not None and attribute.domain == 'POINT' and attribute.data_type == 'INT'
            and attribute.name.startswith(PART_PREFIX) and len(attribute.name) > len(PART_PREFIX))


def _selection_attributes(mesh):
    groups = _groups()
    return [attribute for attribute in mesh.attributes
            if groups._is_group(attribute) or _is_part(attribute)]


def _name_kind(name):
    for prefix in (_groups().PREFIX, PART_PREFIX):
        if isinstance(name, str) and name.startswith(prefix) and len(name) > len(prefix):
            return prefix
    return None


def _digest(name):
    return hashlib.sha256(name.encode('utf-8')).hexdigest()[:20]


def _marker_name(name, solo=False):
    return (SOLO_PREFIX if solo else HIDDEN_PREFIX) + _digest(name)


def _reserved(name):
    return name.startswith((HIDDEN_PREFIX, SOLO_PREFIX, STATE_PREFIX))


def owned_attribute_names(mesh):
    """Return reserved visibility names, including malformed/orphan metadata.

    This is an inspection helper, not permission to overwrite foreign data.
    Mutating operations validate the complete reserved namespace first.
    """
    return tuple(attribute.name for attribute in mesh.attributes if _reserved(attribute.name))


def _domains(bm):
    return {'POINT': bm.verts, 'EDGE': bm.edges, 'FACE': bm.faces}


def _attribute_type(attribute, domain):
    return attribute.domain == domain and attribute.data_type == 'INT'


def _named_group(mesh, name):
    attribute = mesh.attributes.get(name)
    if not (_groups()._is_group(attribute) or _is_part(attribute)):
        raise ValueError('Select a custom vertex group or part.')
    return attribute


def _read_marker(mesh, name, solo):
    _named_group(mesh, name)
    marker = mesh.attributes.get(_marker_name(name, solo))
    if marker is not None and not _attribute_type(marker, 'POINT'):
        raise ValueError('The custom group visibility metadata has an incompatible type.')
    return marker is not None


def is_hidden(mesh, name):
    """Requested eye state; Solo may override it without changing this flag."""
    return _read_marker(mesh, name, False)


def is_solo(mesh, name):
    return _read_marker(mesh, name, True)


def has_legacy_visibility(mesh):
    """Read Custom_ marker existence without preparing or changing BMesh.

    This supports a conditional restore entry for visibility saved before the
    Parts catalog was separated. It does not inspect or reconcile Part_ state.
    """
    present = False
    for attribute in _groups().group_attributes(mesh):
        for solo in (False, True):
            if _read_marker(mesh, attribute.name, solo):
                present = True
    return present


def _native_layer_names(bm):
    names = {}
    for domain, elements in (*_domains(bm).items(), ('CORNER', bm.loops)):
        for kind in _LAYER_KINDS:
            layers = getattr(elements.layers, kind, None)
            if layers is not None:
                for name in layers.keys():
                    names.setdefault(name, []).append((domain, kind))
    return names


def _catalog(mesh, bm=None, *, renaming=None):
    """Validate every reserved attribute before any native state is written."""
    groups = [attribute.name for attribute in _selection_attributes(mesh)]
    marker_owners = {}
    for name in groups:
        for solo in (False, True):
            marker = _marker_name(name, solo)
            if marker in marker_owners and marker_owners[marker] != (name, solo):
                raise ValueError('Two custom group names conflict in visibility metadata.')
            marker_owners[marker] = (name, solo)
    if renaming is not None:
        old, new = renaming
        if old not in groups and new not in groups:
            raise ValueError('The renamed custom group is missing.')
        for solo in (False, True):
            marker_owners.setdefault(_marker_name(old, solo), (old, solo))
            marker_owners.setdefault(_marker_name(new, solo), (new, solo))
    known = {name: 'POINT' for name in marker_owners}
    known.update({name: domain for domain, name in STATE_NAMES.items()})
    attributes = {}
    for attribute in mesh.attributes:
        if not _reserved(attribute.name):
            continue
        domain = known.get(attribute.name)
        if domain is None:
            raise ValueError('Orphan or unrecognized custom group visibility metadata; keep the mesh unchanged.')
        if not _attribute_type(attribute, domain):
            raise ValueError('The custom group visibility metadata has an incompatible type.')
        attributes[attribute.name] = attribute
    if bm is not None:
        domains = _domains(bm)
        native_names = _native_layer_names(bm)
        for name, locations in native_names.items():
            if not _reserved(name):
                continue
            domain = known.get(name)
            if domain is None or locations != [(domain, 'int')]:
                raise ValueError('The custom group visibility metadata conflicts with native custom data.')
            if name not in attributes or domains[domain].layers.int.get(name) is None:
                raise ValueError('The custom group visibility metadata is out of date.')
        for name, attribute in attributes.items():
            layer = domains[attribute.domain].layers.int.get(name)
            if layer is None:
                raise ValueError('The custom group visibility metadata is missing from Edit Mode.')
            if name in STATE_NAMES.values():
                if any(element[layer] < 0 or element[layer] & ~_VALID_BITS
                       for element in domains[attribute.domain]):
                    raise ValueError('The custom group visibility snapshot is malformed.')
            elif any(vertex[layer] != 0 for vertex in bm.verts):
                raise ValueError('The custom group visibility marker is malformed.')
    else:
        for name, attribute in attributes.items():
            if name in STATE_NAMES.values():
                if any(item.value < 0 or item.value & ~_VALID_BITS for item in attribute.data):
                    raise ValueError('The custom group visibility snapshot is malformed.')
            elif any(item.value != 0 for item in attribute.data):
                raise ValueError('The custom group visibility marker is malformed.')
    states = set(STATE_NAMES.values()) & set(attributes)
    if states and states != set(STATE_NAMES.values()):
        raise ValueError('The custom group visibility snapshot is incomplete.')
    hidden = {name for marker, (name, solo) in marker_owners.items()
              if marker in attributes and not solo}
    solo = {name for marker, (name, solo) in marker_owners.items()
            if marker in attributes and solo}
    if (hidden or solo) and not states:
        raise ValueError('The custom group visibility snapshot is missing.')
    if states and not (hidden or solo):
        raise ValueError('Orphan custom group visibility snapshot; keep the mesh unchanged.')
    return groups, hidden, solo, attributes


def _prepare(context):
    mesh = _groups()._mesh(context, edit=True)
    bm = bmesh.from_edit_mesh(mesh)
    catalog = _catalog(mesh, bm)
    return mesh, bm, catalog


def preflight(context):
    """Validate visibility before a caller writes native group membership."""
    _prepare(context)


def _baseline(elements, layer):
    result = []
    for element in elements:
        value = element[layer] if layer is not None else 0
        current = bool(element.hide)
        if not value & _CAPTURED or current != bool(value & _LAST_APPLIED):
            # New topology starts with native default custom data.  Capture its
            # current state conservatively instead of assigning an old index.
            result.append(current)
        else:
            result.append(bool(value & _BASELINE))
    return result


def _plan(bm, hidden, solo):
    domains = _domains(bm)
    baselines = {domain: _baseline(elements, elements.layers.int.get(STATE_NAMES[domain]))
                 for domain, elements in domains.items()}
    # A native H can newly hide a visible vertex whose incident edges/faces
    # were already hidden by the overlay. Those flags do not differ from the
    # last overlay result, so reconcile their baseline through incidence too.
    # Retain independently hidden edges/faces while propagating only upwards.
    vertex_baselines = dict(zip(bm.verts, baselines['POINT']))
    edge_baselines = {
        edge: baseline or any(vertex_baselines[vertex] for vertex in edge.verts)
        for edge, baseline in zip(bm.edges, baselines['EDGE'])}
    baselines['EDGE'] = [edge_baselines[edge] for edge in bm.edges]
    baselines['FACE'] = [
        baseline or any(vertex_baselines[vertex] for vertex in face.verts)
        or any(edge_baselines[edge] for edge in face.edges)
        for face, baseline in zip(bm.faces, baselines['FACE'])]
    if not hidden and not solo:
        return baselines, baselines
    names = solo or hidden
    layers = [bm.verts.layers.int.get(name) for name in names]
    if any(layer is None for layer in layers):
        raise ValueError('A custom group membership layer is missing.')
    vertex_targets = {}
    for vertex, baseline in zip(bm.verts, baselines['POINT']):
        member = any(vertex[layer] != 0 for layer in layers)
        vertex_targets[vertex] = baseline or (not member if solo else member)
    edge_targets = {edge: baseline or any(vertex_targets[vertex] for vertex in edge.verts)
                    for edge, baseline in zip(bm.edges, baselines['EDGE'])}
    face_targets = {face: baseline or any(vertex_targets[vertex] for vertex in face.verts)
                   or any(edge_targets[edge] for edge in face.edges)
                    for face, baseline in zip(bm.faces, baselines['FACE'])}
    targets = {'POINT': [vertex_targets[vertex] for vertex in bm.verts],
               'EDGE': [edge_targets[edge] for edge in bm.edges],
               'FACE': [face_targets[face] for face in bm.faces]}
    return baselines, targets


def _remove_layer(elements, name):
    layer = elements.layers.int.get(name)
    if layer is not None:
        elements.layers.int.remove(layer)


def _new_layer(elements, name):
    layer = elements.layers.int.new(name)
    if layer.name != name:
        elements.layers.int.remove(layer)
        raise ValueError('The custom group visibility metadata name is occupied.')
    return layer


def _write_flags(domains, flags):
    # Blender's select setter ignores hidden elements. Unhide in memory first,
    # restore selection from faces down to vertices (setters propagate down),
    # then apply the complete hide plan. No intermediate state is published.
    for elements in domains.values():
        for element in elements:
            element.hide = False
    for domain in ('FACE', 'EDGE', 'POINT'):
        for element, (_hidden, selected) in zip(domains[domain], flags[domain]):
            element.select = selected
    for domain in ('POINT', 'EDGE', 'FACE'):
        for element, (hidden, _selected) in zip(domains[domain], flags[domain]):
            element.hide = hidden


def _apply(mesh, bm, catalog, hidden, solo):
    _groups_in_catalog, previous_hidden, previous_solo, attributes = catalog
    if not hidden and not solo and not attributes:
        return
    baselines, targets = _plan(bm, hidden, solo)
    domains = _domains(bm)
    active = mesh.attributes.active.name if mesh.attributes.active else ''
    # Adding/removing custom-data layers can invalidate Python BMElement
    # wrappers. Retain only scalar flags and reacquire elements when writing.
    before_flags = {domain: [(bool(element.hide), bool(element.select)) for element in elements]
                    for domain, elements in domains.items()}
    before_state = {}
    for domain, elements in domains.items():
        layer = elements.layers.int.get(STATE_NAMES[domain])
        if layer is not None:
            before_state[domain] = [element[layer] for element in elements]
    before_markers = {_marker_name(name) for name in previous_hidden}
    before_markers.update(_marker_name(name, True) for name in previous_solo)
    markers = {_marker_name(name) for name in hidden}
    markers.update(_marker_name(name, True) for name in solo)

    def restore():
        for name in markers | before_markers:
            if name not in before_markers:
                _remove_layer(bm.verts, name)
            elif bm.verts.layers.int.get(name) is None:
                _new_layer(bm.verts, name)
        for domain, elements in domains.items():
            name = STATE_NAMES[domain]
            if domain not in before_state:
                _remove_layer(elements, name)
            else:
                layer = elements.layers.int.get(name) or _new_layer(elements, name)
                for element, value in zip(elements, before_state[domain]):
                    element[layer] = value
        _write_flags(domains, before_flags)

    try:
        # All collision, membership and snapshot checks completed above.
        for name in sorted(markers - before_markers):
            _new_layer(bm.verts, name)
        for name in before_markers - markers:
            _remove_layer(bm.verts, name)
        for domain, elements in domains.items():
            name = STATE_NAMES[domain]
            layer = elements.layers.int.get(name)
            if markers:
                if layer is None:
                    layer = _new_layer(elements, name)
                for element, baseline, target in zip(elements, baselines[domain], targets[domain]):
                    element[layer] = _CAPTURED | (_BASELINE if baseline else 0) | (_LAST_APPLIED if target else 0)
            elif layer is not None:
                _remove_layer(elements, name)
        # Keep visible selections, deselect hidden elements before hiding them.
        # hide_set() would propagate into neighbors outside the explicit plan.
        flags = {domain: [(target, selected and not target)
                          for (_old_hide, selected), target
                          in zip(before_flags[domain], targets[domain])]
                 for domain in domains}
        _write_flags(domains, flags)
        bm.select_history.validate()
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    except Exception:
        restore()
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        raise
    finally:
        _groups()._activate(mesh, active)


def toggle_hidden(context, name):
    mesh, bm, catalog = _prepare(context)
    _named_group(mesh, name)
    hidden, solo = set(catalog[1]), set(catalog[2])
    hidden.symmetric_difference_update({name})
    _apply(mesh, bm, catalog, hidden, solo)


def toggle_solo(context, name):
    mesh, bm, catalog = _prepare(context)
    _named_group(mesh, name)
    hidden, solo = set(catalog[1]), set(catalog[2])
    solo.symmetric_difference_update({name})
    _apply(mesh, bm, catalog, hidden, solo)


def refresh(context):
    """Recompute after an explicit membership edit; unused meshes stay untouched."""
    mesh, bm, catalog = _prepare(context)
    _apply(mesh, bm, catalog, set(catalog[1]), set(catalog[2]))


def refresh_custom(context):
    """Refresh legacy Custom_ requests; a Parts-only overlay stays untouched.

    The fast path only reads the native catalog and corresponding marker names.
    It does not prepare BMesh, reconcile manual hides, validate a different
    catalog's metadata, or issue an Edit Mode update.
    """
    groups = _groups()
    mesh = groups._mesh(context, edit=True)
    if not has_legacy_visibility(mesh):
        return
    refresh(context)


def refresh_parts(context):
    """Refresh Parts requests; a legacy-only overlay stays strictly untouched.

    As with refresh_custom, the no-request path reads membership names and their
    own markers only, without preparing BMesh or reconciling another overlay.
    """
    mesh = _groups()._mesh(context, edit=True)
    if not any(_read_marker(mesh, attribute.name, solo)
               for attribute in mesh.attributes if _is_part(attribute)
               for solo in (False, True)):
        return
    refresh(context)


def _reset_catalog_visibility(context, prefix):
    mesh, bm, catalog = _prepare(context)
    hidden = {name for name in catalog[1] if not name.startswith(prefix)}
    solo = {name for name in catalog[2] if not name.startswith(prefix)}
    if hidden == catalog[1] and solo == catalog[2]:
        # This catalog has no requests. In particular, an empty Parts reset
        # must not reconcile another catalog's still-active legacy overlay.
        return
    _apply(mesh, bm, catalog, hidden, solo)


def reset_parts_visibility(context):
    """Clear only Parts requests, keeping legacy Custom_ requests in effect."""
    _reset_catalog_visibility(context, PART_PREFIX)


def reset_legacy_visibility(context):
    """Explicitly clear old Custom_ requests without clearing Parts requests."""
    _reset_catalog_visibility(context, _groups().PREFIX)


def reset_visibility(context):
    """Explicitly restore the one manual baseline, including legacy requests."""
    mesh, bm, catalog = _prepare(context)
    _apply(mesh, bm, catalog, set(), set())


def before_delete(context, name):
    """Remove an overlay while its membership still exists, before group deletion."""
    mesh = _groups()._mesh(context)
    _named_group(mesh, name)
    if not (_read_marker(mesh, name, False) or _read_marker(mesh, name, True)):
        # This membership is not referenced by the overlay. In particular,
        # deleting a Custom_ selection must not recompute a Parts-only overlay.
        return
    if not mesh.is_editmode:
        catalog = _catalog(mesh)
        if not catalog[3]:
            return
        raise ValueError('Use Mesh Edit Mode to restore custom group visibility before deleting a group.')
    mesh, bm, catalog = _prepare(context)
    hidden, solo = set(catalog[1]), set(catalog[2])
    hidden.discard(name)
    solo.discard(name)
    _apply(mesh, bm, catalog, hidden, solo)


def preflight_rename(mesh, old, new):
    """Call before renaming membership; never overwrite another marker."""
    kind = _name_kind(old)
    if kind is None or _name_kind(new) != kind:
        raise ValueError('Keep the selection in its original Custom or Parts catalog.')
    if old != new and mesh.attributes.get(old) is not None and mesh.attributes.get(new) is not None:
        raise ValueError('The renamed custom group name is already used.')
    bm = bmesh.from_edit_mesh(mesh) if mesh.is_editmode else None
    _catalog(mesh, bm, renaming=(old, new))
    for solo in (False, True):
        source, target = _marker_name(old, solo), _marker_name(new, solo)
        if source != target and mesh.attributes.get(target) is not None:
            raise ValueError('The renamed custom group conflicts with existing visibility metadata.')


def rename_state(mesh, old, new):
    """Migrate native marker names after the already-preflighted group rename."""
    preflight_rename(mesh, old, new)
    if old == new:
        return
    markers = []
    for solo in (False, True):
        attribute = mesh.attributes.get(_marker_name(old, solo))
        if attribute is not None:
            markers.append((attribute, attribute.name, _marker_name(new, solo)))
    try:
        for attribute, _source, target in markers:
            attribute.name = target
            if attribute.name != target:
                raise ValueError('The custom group visibility marker could not be renamed.')
    except Exception:
        for attribute, source, _target in markers:
            attribute.name = source
        raise
