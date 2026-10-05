"""Stable, source-owned identities for independent Hair chains.

Reads never write artist data. Initialize/reconcile/manual_pair change only one
source JSON property, transactionally. A topology signature is an identity input,
never a request to find the nearest replacement strand. Native topology pairs
must cover the complete strand and every root-to-tip layer reciprocally.
"""
import hashlib
import json
import math
import uuid
from collections import Counter, defaultdict

import bpy

from . import hair_bones_rig as hair

REGISTRY_KEY = 'character_designer_hair_strands_v1'
VERSION = 1
AXIS = 'X'
SIDES = frozenset({'L', 'R', 'C', 'U'})
PROOFS = frozenset({'MIRROR', 'TOPOLOGY', 'GEOMETRY', 'GRAPH', 'MANUAL', 'NONE'})
MAPPED_PROOFS = frozenset({'TOPOLOGY', 'GEOMETRY', 'GRAPH'})
INCIDENCE_PROOFS = frozenset({'GEOMETRY', 'GRAPH'})


class HairStrandRegistryError(ValueError):
    """Incomplete or changed source ownership prevents a safe registry read."""


def _uid(value):
    try:
        return uuid.UUID(str(value)).hex
    except (ValueError, TypeError, AttributeError) as exc:
        raise HairStrandRegistryError('The saved Hair source UUID is invalid.') from exc


def _identity(source_uid, signature):
    namespace = uuid.UUID(source_uid)
    strand_id = uuid.uuid5(namespace, 'strand:' + signature).hex
    chain_id = uuid.uuid5(namespace, 'chain:' + signature).hex
    return strand_id, chain_id


def _pair_id(source_uid, first, second):
    return uuid.uuid5(uuid.UUID(source_uid), 'pair:' + ':'.join(sorted((first, second)))).hex


def _geometry(source):
    if not isinstance(source, bpy.types.Object) or source.type != 'MESH':
        raise HairStrandRegistryError('Choose the original bound Hair mesh.')
    if source.mode == 'EDIT':
        raise HairStrandRegistryError('Finish Hair Edit Mode before reading strand ownership.')
    mesh = source.data
    points = tuple(tuple(vertex.co) for vertex in mesh.vertices)
    if mesh.shape_keys and mesh.shape_keys.reference_key:
        points = tuple(tuple(point.co) for point in mesh.shape_keys.reference_key.data)
    edges = tuple(tuple(edge.vertices) for edge in mesh.edges)
    faces = tuple(tuple(face.vertices) for face in mesh.polygons)
    if any(not all(math.isfinite(value) for value in point) for point in points):
        raise HairStrandRegistryError('Hair Basis contains non-finite coordinates.')
    payload = (len(points), tuple(sorted(tuple(sorted(edge)) for edge in edges)), faces)
    topology = hashlib.sha256(repr(payload).encode('ascii')).hexdigest()
    return points, edges, faces, topology


def _rest(bone, order):
    """Relations use chain indices, so a bone rename is not a structural edit."""
    parent = bone.parent
    return {'head': list(bone.head_local), 'tail': list(bone.tail_local),
            'matrix': [list(row) for row in bone.matrix_local],
            'parent_index': order.get(parent.name, -1) if parent else -1,
            'connected': bool(bone.use_connect), 'deform': bool(bone.use_deform),
            'inherit_scale': bone.inherit_scale,
            'inherit_rotation': bool(bone.use_inherit_rotation),
            'local_location': bool(bone.use_local_location)}


def _anchor(bone):
    if bone is None:
        return None
    return {'name': bone.name, 'matrix': [list(row) for row in bone.matrix_local],
            'head': list(bone.head_local), 'tail': list(bone.tail_local),
            'connected': bool(bone.use_connect), 'deform': bool(bone.use_deform)}


def _same_anchor(before, actual):
    if before is None or actual is None:
        return before is actual
    return {key: value for key, value in before.items() if key != 'name'} == {
        key: value for key, value in actual.items() if key != 'name'}


def _live(source, *, check_layers=False):
    points, edges, faces, topology = _geometry(source)
    record = hair._read_records(source)
    armature = source.get(hair.RIG_KEY)
    if not record or not isinstance(armature, bpy.types.Object) or armature.type != 'ARMATURE':
        raise HairStrandRegistryError('Hair needs its existing owned bone-chain binding first.')
    if armature.mode == 'EDIT':
        raise HairStrandRegistryError('Finish Armature Edit Mode before reading strand Rest proof.')
    modifiers = tuple(item for item in source.modifiers if item.type == 'ARMATURE')
    if (len(modifiers) != 1 or modifiers[0].object != armature
            or not modifiers[0].use_vertex_groups or modifiers[0].use_bone_envelopes
            or modifiers[0].vertex_group or modifiers[0].use_multi_modifier
            or source.parent_type == 'BONE'
            or source.parent and source.parent.type == 'ARMATURE' and source.parent != armature):
        raise HairStrandRegistryError('The saved Hair source no longer has its owned ordinary Armature binding.')
    source_uid = _uid(record['source_id'])
    signatures = {chain['signature'] for chain in record['chains']}
    owned = {}
    for bone in armature.data.bones:
        if bone.get(hair.SOURCE_KEY) == source:
            if (bone.get(hair.OWNER_KEY) != hair.OWNER_VALUE
                    or bone.get(hair.SIGNATURE_KEY) not in signatures or not bone.use_deform):
                raise HairStrandRegistryError('Hair bone source ownership changed; restore it before reconciling.')
            owned.setdefault(bone.get(hair.SIGNATURE_KEY), []).append(bone)
    strands = []
    for order, chain in enumerate(record['chains']):
        if chain.get('members'):
            raise HairStrandRegistryError('Legacy shared-chain Hair cannot be represented as independent strands.')
        signature = chain['signature']
        bones = owned.get(signature, [])
        if len(bones) != chain['count'] or not bones or len(chain['rest']) != len(bones):
            raise HairStrandRegistryError('An owned Hair chain has missing, extra or reassigned bones.')
        names = {bone.name for bone in bones}
        roots = [bone for bone in bones if bone.parent is None or bone.parent.name not in names]
        if len(roots) != 1:
            raise HairStrandRegistryError('Hair chain parenting is no longer one ordered root-to-tip path.')
        root, ordered = roots[0], []
        current = root
        while current is not None:
            ordered.append(current)
            children = [bone for bone in bones if bone.parent == current]
            if len(children) > 1:
                raise HairStrandRegistryError('A Hair chain was branched; restore its ordered parenting.')
            current = children[0] if children else None
        if len(ordered) != len(bones):
            raise HairStrandRegistryError('Hair chain parenting contains a disconnected component.')
        for index, bone in enumerate(ordered):
            expected = chain['rest'][index]
            if bone.use_connect != expected['connected'] or index and bone.parent != ordered[index - 1]:
                raise HairStrandRegistryError('Hair connection or ordered parent relationships changed.')
            # The existing registry can prove a renamed external anchor. Before
            # initialization the binding must still resolve its recorded one.
            if not index and root.parent is None and record['parent']:
                raise HairStrandRegistryError('Hair attachment lost its recorded parent.')
            if not index and root.parent is not None and root.parent.name != record['parent']:
                if record['parent'] in armature.data.bones or REGISTRY_KEY not in source:
                    raise HairStrandRegistryError('Hair attachment changed; restore the recorded parent.')
                old = _saved(source)
                previous = next((item for item in old['strands'] if item['signature'] == signature), None)
                if previous is None or not _same_anchor(previous['anchor'], _anchor(root.parent)):
                    raise HairStrandRegistryError('The renamed Hair attachment no longer has its saved Rest proof.')
            if source.vertex_groups.get(bone.name) is None:
                raise HairStrandRegistryError('An owned Hair bone has lost its corresponding vertex group.')
        layers = [list(layer) for layer in chain['layers']]
        vertices = sorted(chain['vertices'])
        flat = [vertex for layer in layers for vertex in layer]
        if (len(layers) < 2 or any(not layer for layer in layers)
                or len(flat) != len(set(flat)) or set(flat) != set(vertices)
                or any(type(vertex) is not int or not 0 <= vertex < len(points) for vertex in flat)):
            raise HairStrandRegistryError('The owned Hair chain has incomplete source layers or vertices.')
        if check_layers:
            from . import hair_bones_groups as groups
            from . import hair_bones_topology as topology_service
            # This reuses the conservative source layer validator, not spatial
            # strand discovery. Existing binding signatures can be opaque, and
            # virtual Mirror chains deliberately share the original layers.
            with groups._mesh(source) as bm:
                checked = topology_service._strict_plan(source, bm, layers, edges, explicit_direction=True)
                if (checked is None or checked['layers'] != tuple(tuple(sorted(layer)) for layer in layers)
                        or set(checked['vertices']) != set(vertices)):
                    raise HairStrandRegistryError('The saved Hair chain no longer has its exact source cross-sections.')
        strand_id, chain_id = _identity(source_uid, signature)
        indices = {bone.name: index for index, bone in enumerate(ordered)}
        strands.append({'strand_id': strand_id, 'chain_id': chain_id,
            'signature': signature, 'order': order,
            'bones': [bone.name for bone in ordered],
            'rest': [_rest(bone, indices) for bone in ordered], 'anchor': _anchor(root.parent),
            'vertices': vertices, 'layers': layers,
            'pair_id': None, 'mirror_id': None, 'side': 'U', 'pair_proof': 'NONE',
            'vertex_map': [], 'boundary_map': []})
    return {'version': VERSION, 'source_uid': source_uid, 'topology': topology,
            'axis': AXIS, 'strands': strands, 'diagnostics': []}, record, (points, edges, faces)


def _link(data, first, second, proof):
    pair_id = _pair_id(data['source_uid'], first['strand_id'], second['strand_id'])
    for strand, other, side in ((first, second, 'L'), (second, first, 'R')):
        strand.update(pair_id=pair_id, mirror_id=other['strand_id'], side=side, pair_proof=proof)


def _center(data, strand, proof):
    strand.update(pair_id=_pair_id(data['source_uid'], strand['strand_id'], strand['strand_id']),
                  mirror_id=strand['strand_id'], side='C', pair_proof=proof)


def _compatible(first, second):
    return (len(first['bones']) == len(second['bones'])
            and len(first['layers']) == len(second['layers'])
            and all(len(a) == len(b) for a, b in zip(first['layers'], second['layers'])))


def _mirror_provenance(data, record, source):
    if not record.get('mirror_layout'):
        return
    from . import hair_bones_mirror
    modifier = hair_bones_mirror.preflight(source)
    if record['mirror_layout'] != hair_bones_mirror.LAYOUT or modifier is None:
        raise HairStrandRegistryError('The saved Hair Mirror binding no longer has its native X Mirror.')
    armature_modifiers = [item for item in source.modifiers
                         if item.type == 'ARMATURE' and item.object == source.get(hair.RIG_KEY)]
    if (len(armature_modifiers) != 1 or modifier.mirror_object is not None
            or not modifier.use_mirror_vertex_groups
            or tuple(source.modifiers).index(modifier) >= tuple(source.modifiers).index(armature_modifiers[0])):
        raise HairStrandRegistryError('The saved Hair Mirror must precede its owned Armature with group flipping enabled.')
    by_signature = {strand['signature']: strand for strand in data['strands']}
    raw = {chain['signature']: chain for chain in record['chains']}
    for signature, chain in raw.items():
        strand = by_signature[signature]
        if chain.get('mirror_side') == 'C' and not chain.get('mirror_of'):
            _center(data, strand, 'MIRROR')
        elif chain.get('mirror_of'):
            partner = by_signature.get(chain['mirror_of'])
            partner_raw = raw.get(chain['mirror_of'])
            if (partner is None or partner_raw is None or not _compatible(strand, partner)
                    or partner_raw.get('mirror_of')
                    or {chain.get('mirror_side'), partner_raw.get('mirror_side')} != {'L', 'R'}
                    or strand['layers'] != partner['layers'] or strand['vertices'] != partner['vertices']
                    or strand['pair_proof'] != 'NONE' or partner['pair_proof'] != 'NONE'):
                raise HairStrandRegistryError('The saved Hair Mirror provenance is incomplete or conflicting.')
            left, right = (strand, partner) if chain['mirror_side'] == 'L' else (partner, strand)
            _link(data, left, right, 'MIRROR')
    if any(strand['pair_proof'] != 'MIRROR' for strand in data['strands']):
        raise HairStrandRegistryError('The saved Hair Mirror provenance does not cover every owned chain.')


def _cyclic_face(face):
    face = tuple(face)
    return min(order[offset:] + order[:offset] for order in (face, tuple(reversed(face)))
               for offset in range(len(face)))


def _mesh_graph(coordinates, edges, faces):
    edge_ids, face_ids, adjacency = defaultdict(set), defaultdict(set), defaultdict(set)
    edges, faces = tuple(tuple(edge) for edge in edges), tuple(tuple(face) for face in faces)
    for index, (first, second) in enumerate(edges):
        edge_ids[first].add(index)
        edge_ids[second].add(index)
        adjacency[first].add(second)
        adjacency[second].add(first)
    for index, face in enumerate(faces):
        for vertex in face:
            face_ids[vertex].add(index)
    return {'count': len(coordinates), 'edges': edges, 'faces': faces,
            'edge_ids': edge_ids, 'face_ids': face_ids, 'adjacency': adjacency}


def _domain_graph(vertices, graph):
    domain = set(vertices)
    edge_ids = set().union(*(graph['edge_ids'][index] for index in domain))
    face_ids = set().union(*(graph['face_ids'][index] for index in domain))
    edges = tuple(graph['edges'][index] for index in edge_ids)
    faces = tuple(graph['faces'][index] for index in face_ids)
    ring = domain | {index for edge in edges for index in edge} | {index for face in faces for index in face}
    return {'domain': domain, 'ring': ring, 'incident_edges': edges, 'incident_faces': faces,
            'edges': tuple(edge for edge in edges if set(edge) <= domain),
            'faces': tuple(face for face in faces if set(face) <= domain)}


def _graph_match(first, second, lookup, graph, *, incidence=False):
    """Prove edge/face bijection, including every boundary incidence when asked."""
    a, b = _domain_graph(first['vertices'], graph), _domain_graph(second['vertices'], graph)
    if (any(index not in lookup or not 0 <= lookup[index] < graph['count'] for index in a['domain'])
            or {lookup[index] for index in a['domain']} != b['domain']):
        return None
    for key, canonical in (('edges', lambda edge: tuple(sorted(edge))), ('faces', _cyclic_face)):
        if Counter(canonical(tuple(lookup[index] for index in item)) for item in a[key]) != Counter(
                canonical(item) for item in b[key]):
            return None
    if not incidence:
        return []
    if (any(index not in lookup or not 0 <= lookup[index] < graph['count'] for index in a['ring'])
            or {lookup[index] for index in a['ring']} != b['ring']
            or any(len(graph['adjacency'][index]) != len(graph['adjacency'][lookup[index]]) for index in a['domain'])):
        return None
    for key, canonical in (('incident_edges', lambda edge: tuple(sorted(edge))), ('incident_faces', _cyclic_face)):
        if Counter(canonical(tuple(lookup[index] for index in item)) for item in a[key]) != Counter(
                canonical(item) for item in b[key]):
            return None
    return [[index, lookup[index]] for index in sorted(a['ring'] - a['domain'])]


def _exact_lookup(coordinates):
    """Exact source-local reflection; duplicate coordinates stay unresolved."""
    buckets = defaultdict(list)
    for index, coordinate in enumerate(coordinates):
        buckets[tuple(coordinate)].append(index)
    lookup = {}
    for index, (x, y, z) in enumerate(coordinates):
        original, reflected = buckets[(x, y, z)], buckets.get((-x, y, z), ())
        if len(original) == len(reflected) == 1:
            lookup[index] = reflected[0]
    return lookup


def _lookup_pairs(data, coordinates, lookup, *, proof, graph=None):
    if (any(type(index) is not int or type(other) is not int or not 0 <= index < len(coordinates)
            or not 0 <= other < len(coordinates) or lookup.get(other) != index for index, other in lookup.items())):
        raise HairStrandRegistryError('Hair vertex correspondence is not a reciprocal one-to-one map.')
    eligible = [strand for strand in data['strands'] if strand['pair_proof'] == 'NONE']
    candidates, boundaries = {}, {}
    for strand in eligible:
        vertices = strand['vertices']
        if any(vertex not in lookup for vertex in vertices):
            continue
        reflected = {lookup[vertex] for vertex in vertices}
        matches = [other for other in eligible if set(other['vertices']) == reflected
                   and _compatible(strand, other)
                   and all({lookup[vertex] for vertex in layer} == set(other_layer)
                           for layer, other_layer in zip(strand['layers'], other['layers']))]
        if len(matches) != 1:
            continue
        boundary = _graph_match(strand, matches[0], lookup, graph, incidence=proof in INCIDENCE_PROOFS) if graph else []
        if boundary is None or proof in INCIDENCE_PROOFS and graph is None:
            continue
        candidates[strand['strand_id']] = matches[0]
        boundaries[strand['strand_id']] = boundary
    for strand in eligible:
        partner = candidates.get(strand['strand_id'])
        if partner is None or candidates.get(partner['strand_id']) is not strand:
            continue
        if strand['pair_proof'] != 'NONE':
            continue
        if partner is strand:
            _center(data, strand, proof)
        else:
            first = {math.copysign(1., coordinates[index][0]) for index in strand['vertices']
                     if lookup[index] != index and coordinates[index][0] != 0.}
            second = {math.copysign(1., coordinates[index][0]) for index in partner['vertices']
                      if lookup[index] != index and coordinates[index][0] != 0.}
            if first != {1.} or second != {-1.}:
                if first != {-1.} or second != {1.}:
                    continue
                left, right = partner, strand
            else:
                left, right = strand, partner
            _link(data, left, right, proof)
        for member in (strand,) if partner is strand else (strand, partner):
            member['vertex_map'] = [[index, lookup[index]] for index in member['vertices']]
            member['boundary_map'] = boundaries[member['strand_id']]


def _topology_pairs(data, coordinates, vertex_pairs, *, proof='TOPOLOGY', graph=None):
    """Consume only a trusted reciprocal vertex map; never search coordinates."""
    indices = tuple(vertex_pairs.centerline) + tuple(index for pair in vertex_pairs.pairs for index in pair)
    if (any(type(index) is not int or not 0 <= index < len(coordinates) for index in indices)
            or len(indices) != len(set(indices))):
        raise HairStrandRegistryError('The native Hair vertex correspondence is not one-to-one.')
    lookup = {index: index for index in vertex_pairs.centerline}
    for negative, positive in vertex_pairs.pairs:
        if negative in lookup or positive in lookup or negative == positive:
            raise HairStrandRegistryError('The native Hair vertex correspondence is not one-to-one.')
        lookup[negative], lookup[positive] = positive, negative
    _lookup_pairs(data, coordinates, lookup, proof=proof, graph=graph)


def _numbers(value, length):
    return (isinstance(value, list) and len(value) == length
            and all(type(item) in {int, float} and math.isfinite(item) for item in value))


def _rest_schema(value, index=None):
    if (not isinstance(value, dict) or not _numbers(value.get('head'), 3) or not _numbers(value.get('tail'), 3)
            or not isinstance(value.get('matrix'), list) or len(value['matrix']) != 4
            or not all(_numbers(row, 4) for row in value['matrix'])
            or type(value.get('connected')) is not bool or type(value.get('deform')) is not bool):
        return False
    if index is None:
        return isinstance(value.get('name'), str) and bool(value['name'])
    return (type(value.get('parent_index')) is int and value['parent_index'] == index - 1
            and isinstance(value.get('inherit_scale'), str) and bool(value['inherit_scale'])
            and type(value.get('inherit_rotation')) is bool and type(value.get('local_location')) is bool)


def _validate_links(data):
    if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != VERSION
            or data.get('axis') != AXIS or not isinstance(data.get('strands'), list) or not data['strands']
            or not isinstance(data.get('diagnostics'), list)
            or not all(isinstance(item, str) for item in data['diagnostics'])
            or not isinstance(data.get('topology'), str) or len(data['topology']) != 64
            or any(letter not in '0123456789abcdef' for letter in data['topology'])):
        raise HairStrandRegistryError('The saved Hair strand registry has an unsupported schema.')
    source_uid = _uid(data.get('source_uid'))
    if source_uid != data['source_uid']:
        raise HairStrandRegistryError('The Hair strand registry UUID must use its canonical saved identity.')
    by_id, orders = {}, set()
    for strand in data['strands']:
        if (not isinstance(strand, dict) or not isinstance(strand.get('signature'), str) or not strand['signature']
                or not {'pair_id', 'mirror_id', 'side', 'pair_proof'}.issubset(strand)
                or type(strand.get('order')) is not int or strand['order'] < 0 or strand['order'] in orders
                or not isinstance(strand.get('bones'), list) or not strand['bones']
                or not all(isinstance(name, str) and name for name in strand['bones'])
                or len(strand['bones']) != len(set(strand['bones']))
                or not isinstance(strand.get('rest'), list) or len(strand['rest']) != len(strand['bones'])
                or not all(_rest_schema(proof, index) for index, proof in enumerate(strand['rest']))
                or 'anchor' not in strand or strand['anchor'] is not None and not _rest_schema(strand['anchor'])
                or not isinstance(strand.get('vertices'), list) or not strand['vertices']
                or any(type(index) is not int or index < 0 for index in strand['vertices'])
                or strand['vertices'] != sorted(set(strand['vertices']))
                or not isinstance(strand.get('layers'), list) or len(strand['layers']) < 2
                or any(not isinstance(layer, list) or not layer for layer in strand['layers'])
                or not isinstance(strand.get('vertex_map'), list)
                or not isinstance(strand.get('boundary_map', []), list)
                or strand.get('pair_proof') in INCIDENCE_PROOFS and 'boundary_map' not in strand):
            raise HairStrandRegistryError('Hair strand source layers or ordered Rest proof are incomplete.')
        flat = [index for layer in strand['layers'] for index in layer]
        if (any(type(index) is not int or index < 0 for index in flat)
                or len(flat) != len(set(flat)) or set(flat) != set(strand['vertices'])
                or any(not isinstance(pair, list) or len(pair) != 2
                       or any(type(index) is not int or index < 0 for index in pair)
                       for pair in strand['vertex_map'] + strand.get('boundary_map', []))):
            raise HairStrandRegistryError('Hair strand vertex ownership or topology correspondence is invalid.')
        expected = _identity(source_uid, strand['signature'])
        if ((strand.get('strand_id'), strand.get('chain_id')) != expected
                or strand['strand_id'] in by_id or strand.get('side') not in SIDES
                or strand.get('pair_proof') not in PROOFS):
            raise HairStrandRegistryError('Hair strand identities or pair state are invalid.')
        by_id[strand['strand_id']] = strand
        orders.add(strand['order'])
    for strand in data['strands']:
        if strand['pair_proof'] == 'NONE':
            if (strand['side'] != 'U' or strand.get('pair_id') is not None or strand.get('mirror_id') is not None
                    or strand['vertex_map'] or strand.get('boundary_map')):
                raise HairStrandRegistryError('An unmatched Hair strand has a partial pair.')
            continue
        partner = by_id.get(strand.get('mirror_id'))
        if (partner is None or partner.get('mirror_id') != strand['strand_id']
                or partner['pair_proof'] != strand['pair_proof'] or not _compatible(strand, partner)
                or strand.get('pair_id') != _pair_id(source_uid, strand['strand_id'], partner['strand_id'])
                or partner.get('pair_id') != strand['pair_id']
                or ((strand is partner) != (strand['side'] == 'C'))
                or strand is not partner and {strand['side'], partner['side']} != {'L', 'R'}):
            raise HairStrandRegistryError('Hair mirror pairs must be complete, reciprocal and count-compatible.')
        if strand['pair_proof'] in MAPPED_PROOFS:
            mapping = dict(strand['vertex_map'])
            reverse = dict(partner['vertex_map'])
            if (len(mapping) != len(strand['vertex_map']) or set(mapping) != set(strand['vertices'])
                    or set(mapping.values()) != set(partner['vertices'])
                    or any(reverse.get(value) != key for key, value in mapping.items())
                    or any({mapping[index] for index in layer} != set(other)
                           for layer, other in zip(strand['layers'], partner['layers']))):
                raise HairStrandRegistryError('Hair topology pair proof is partial or nonreciprocal.')
            boundary = dict(strand.get('boundary_map', []))
            other_boundary = dict(partner.get('boundary_map', []))
            if (len(boundary) != len(strand.get('boundary_map', []))
                    or len(set(boundary.values())) != len(boundary)
                    or set(boundary) & set(strand['vertices']) or set(boundary.values()) & set(partner['vertices'])
                    or any(other_boundary.get(other) != index for index, other in boundary.items())
                    or strand['pair_proof'] not in INCIDENCE_PROOFS and boundary):
                raise HairStrandRegistryError('Hair boundary correspondence is incomplete or nonreciprocal.')
        elif strand['vertex_map'] or strand.get('boundary_map'):
            raise HairStrandRegistryError('Only proven vertex pairing can store a source correspondence.')
    return data


def _validate_mesh_pairs(data, geometry, *, graph=None):
    graph = graph or _mesh_graph(*geometry)
    by_id = {strand['strand_id']: strand for strand in data['strands']}
    for strand in data['strands']:
        if strand['pair_proof'] not in MAPPED_PROOFS:
            continue
        partner = by_id[strand['mirror_id']]
        lookup = dict(strand['vertex_map'])
        lookup.update(strand.get('boundary_map', []))
        expected = _graph_match(strand, partner, lookup, graph, incidence=strand['pair_proof'] in INCIDENCE_PROOFS)
        if expected is None or expected != strand.get('boundary_map', []):
            raise HairStrandRegistryError('Hair vertex proof no longer preserves complete source edges, faces or boundary incidence.')
    return data


def _saved(source):
    if not isinstance(source, bpy.types.Object) or source.type != 'MESH':
        raise HairStrandRegistryError('Choose the original bound Hair mesh.')
    raw = source.get(REGISTRY_KEY)
    if raw is None:
        return None
    try:
        data = _validate_links(json.loads(raw))
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, HairStrandRegistryError):
            raise
        raise HairStrandRegistryError('The saved Hair strand registry is incomplete.') from exc
    return data


def read(source, validate=True):
    """Return detached data; backend/export use the strict default.

    ``validate=False`` only parses saved schema and reciprocal metadata. It does
    not prove current native source ownership and must not authorize a backend.
    """
    data = _saved(source)
    if data is None or not validate:
        return data
    live, record, geometry = _live(source)
    if live['source_uid'] != data['source_uid'] or live['topology'] != data['topology']:
        raise HairStrandRegistryError('Hair identity or topology changed; explicitly reconcile its strand registry.')
    old = {strand['signature']: strand for strand in data['strands']}
    if set(old) != {strand['signature'] for strand in live['strands']}:
        raise HairStrandRegistryError('Hair strand inventory changed; explicitly reconcile it.')
    for current in live['strands']:
        previous = old[current['signature']]
        if (current['rest'] != previous['rest'] or not _same_anchor(previous['anchor'], current['anchor'])
                or current['layers'] != previous['layers'] or current['vertices'] != previous['vertices']):
            raise HairStrandRegistryError('Hair chain Rest or segmentation changed; explicitly reconcile its proof.')
        previous['bones'] = current['bones']
        previous['anchor'] = current['anchor']
    if any(strand['pair_proof'] == 'MIRROR' for strand in data['strands']):
        _mirror_provenance(live, record, source)
        for current in live['strands']:
            previous = old[current['signature']]
            if any(current[key] != previous[key] for key in ('pair_id', 'mirror_id', 'side', 'pair_proof')):
                raise HairStrandRegistryError('The native Hair Mirror provenance changed; explicitly reconcile it.')
    _validate_mesh_pairs(data, geometry)
    return data


def _write(source, data):
    source[REGISTRY_KEY] = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _commit(source, data):
    if source.library or source.override_library or not source.is_editable:
        raise HairStrandRegistryError('Make Hair local and editable before changing strand settings.')
    _validate_links(data)
    old = source.get(REGISTRY_KEY)
    try:
        _write(source, data)
        return read(source)
    except Exception:
        if old is None:
            source.pop(REGISTRY_KEY, None)
        else:
            source[REGISTRY_KEY] = old
        raise


def initialize(source):
    """Explicit metadata-only setup; never replace an existing registry."""
    if _saved(source) is not None:
        return read(source)
    return reconcile(source)


def _restore_proven_pairs(data, old, geometry, graph):
    """A coordinate/Rest edit never re-guesses an already proven identity."""
    if old is None or old['topology'] != data['topology']:
        return
    _validate_mesh_pairs(old, geometry, graph=graph)
    current = {strand['strand_id']: strand for strand in data['strands']}
    saved = {strand['strand_id']: strand for strand in old['strands']}
    for previous in old['strands']:
        if previous['pair_proof'] == 'NONE':
            continue
        first, second = current.get(previous['strand_id']), current.get(previous['mirror_id'])
        if not first or not second or not _compatible(first, second):
            continue
        if any(member['layers'] != saved[member['strand_id']]['layers']
               or member['vertices'] != saved[member['strand_id']]['vertices'] for member in (first, second)):
            continue
        if first['pair_proof'] != 'NONE' or second['pair_proof'] != 'NONE':
            if first['mirror_id'] != second['strand_id'] or second['mirror_id'] != first['strand_id']:
                raise HairStrandRegistryError('A new Hair proof conflicts with an existing proven mirror identity.')
            continue
        for member in (first,) if first is second else (first, second):
            previous_member = saved[member['strand_id']]
            for key in ('pair_id', 'mirror_id', 'side', 'pair_proof', 'vertex_map', 'boundary_map'):
                member[key] = json.loads(json.dumps(previous_member.get(key, [])))


def reconcile(source):
    """Refresh exact source/Rest proof without transferring settings by position."""
    old = _saved(source)
    data, record, geometry = _live(source, check_layers=True)
    if old is not None and old['source_uid'] != data['source_uid']:
        raise HairStrandRegistryError('Hair source UUID changed; do not reassign its existing strand settings.')
    if old is not None:
        previous_by_id = {strand['strand_id']: strand for strand in old['strands']}
        next_order = max(strand['order'] for strand in old['strands']) + 1
        structural = ('parent_index', 'connected', 'deform', 'inherit_scale', 'inherit_rotation', 'local_location')
        for strand in data['strands']:
            previous = previous_by_id.get(strand['strand_id'])
            if previous is None:
                strand['order'], next_order = next_order, next_order + 1
                continue
            strand['order'] = previous['order']
            if strand['layers'] != previous['layers'] or strand['vertices'] != previous['vertices']:
                raise HairStrandRegistryError('A saved Hair signature was reassigned to different source vertices or layers.')
            # Re-segmenting one proven chain is an explicit binding update. A
            # same-size Rest refresh never authorizes a native relation change.
            if len(previous['rest']) == len(strand['rest']) and any(
                    any(before[key] != actual[key] for key in structural)
                    for before, actual in zip(previous['rest'], strand['rest'])):
                raise HairStrandRegistryError('Hair parenting, connection or inheritance changed; restore the owned setup.')
        data['strands'].sort(key=lambda strand: strand['order'])
    graph = _mesh_graph(*geometry)
    _mirror_provenance(data, record, source)
    _restore_proven_pairs(data, old, geometry, graph)
    if any(strand['pair_proof'] == 'NONE' for strand in data['strands']):
        from . import native_symmetry_pairs
        from .symmetry_pairs import SymmetryPairError
        try:
            pairs = native_symmetry_pairs.build_vertex_pairs(*geometry, axis=AXIS)
            _topology_pairs(data, geometry[0], pairs, graph=graph)
            data['diagnostics'].extend(pairs.diagnostics)
        except SymmetryPairError as exc:
            data['diagnostics'].append(str(exc))
    if any(strand['pair_proof'] == 'NONE' for strand in data['strands']):
        _lookup_pairs(data, geometry[0], _exact_lookup(geometry[0]), proof='GEOMETRY', graph=graph)
        data['diagnostics'].append('Exact source-local X reflection pairs require complete strand and boundary topology.')
    if any(strand['pair_proof'] == 'NONE' for strand in data['strands']):
        from . import symmetry_pairs
        try:
            pairs = symmetry_pairs.build_vertex_pairs(*geometry, centerline_tolerance=0.0)
            _topology_pairs(data, geometry[0], pairs, proof='GRAPH', graph=graph)
            data['diagnostics'].extend(pairs.diagnostics)
        except symmetry_pairs.SymmetryPairError as exc:
            data['diagnostics'].append(str(exc))
    _validate_mesh_pairs(data, geometry, graph=graph)
    return _commit(source, data)


def manual_pair(source, left_id, right_id):
    """Explicit reciprocal pair; arguments declare L/R, never infer nearest."""
    data = read(source)
    if data is None:
        raise HairStrandRegistryError('Initialize Hair strand settings before pairing strands.')
    by_id = {strand['strand_id']: strand for strand in data['strands']}
    left, right = by_id.get(left_id), by_id.get(right_id)
    if left is None or right is None or left is right:
        raise HairStrandRegistryError('Choose two different owned Hair strands for a manual pair.')
    if left['side'] == 'C' or right['side'] == 'C' or not _compatible(left, right):
        raise HairStrandRegistryError('Center strands and different chain/layer counts cannot be manually paired.')
    if any(strand['mirror_id'] not in {None, other['strand_id']}
           for strand, other in ((left, right), (right, left))):
        raise HairStrandRegistryError('A strand already belongs to another proven mirror pair.')
    if any(strand['pair_proof'] not in {'NONE', 'MANUAL'} for strand in (left, right)):
        if left['mirror_id'] == right_id and left['side'] == 'L':
            return data
        raise HairStrandRegistryError('Preserve the verified Hair mirror provenance instead of replacing it.')
    _link(data, left, right, 'MANUAL')
    return _commit(source, data)
