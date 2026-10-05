"""Deterministic Hair motion sidecars, without Blender or backend writes.

Callers must obtain a current ``hair_strand_registry.read(source, validate=True)``
before passing a detached registry dict. A registry reader can instead be passed
directly; its strict read is performed here. This module checks the data/binding
contract, not live mesh ownership. It never discovers pairs or exported bones by
name, position, or proximity, and it never claims FBX bakes Hair simulation.

Rest matrices remain in the source armature's local space and source length unit.
An optional mapping descriptor can carry the worker's actual exported rest data;
it is never synthesized from a guessed axis/scale conversion.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import uuid


SCHEMA = 'cdesigner.hair-motion/1'
SCHEMA_VERSION = 1
EXPORTER_VERSION = '1.0.0'
CONVERSION = {'wiggle': 'wiggle-1.1.2-v1', 'magica': 'magica2-motion-v1'}
MAPPED_PROOFS = frozenset({'TOPOLOGY', 'GEOMETRY', 'GRAPH'})
BOUNDARY_PROOFS = frozenset({'GEOMETRY', 'GRAPH'})
PARAMETERS = {
    'recovery': {'unit': 'normalized', 'range': [0.0, 1.0]},
    'damping': {'unit': 'normalized', 'range': [0.0, 1.0]},
    'gravity': {'unit': 'meters_per_second_squared', 'range': [0.0, 20.0]},
    'stretch': {'unit': 'normalized', 'range': [0.0, 1.0]},
    'depth': {'position': 'normalized_root_0_tip_1', 'mass': 'relative_mass',
              'interpolation': 'piecewise_linear',
              'fields': ['position', 'recovery', 'damping', 'mass', 'gravity']},
}
RANGES = {'recovery': (0.0, 1.0), 'damping': (0.0, 1.0),
          'gravity': (0.0, 20.0), 'stretch': (0.0, 1.0), 'mass': (0.01, 10.0)}
GROUPS = frozenset({'FRONT', 'SIDE', 'BACK', 'UNASSIGNED'})


class HairMotionExportError(ValueError):
    """Incomplete identities or bindings prevent a safe sidecar publication."""


def _text(value, label):
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise HairMotionExportError(f'{label} must be nonempty text without control characters.')
    return value


def _hash(value, label):
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdefABCDEF' for char in value)):
        raise HairMotionExportError(f'{label} must be a complete SHA-256 hex digest.')
    return value.lower()


def _uid(value, label):
    try:
        return uuid.UUID(_text(value, label)).hex
    except (ValueError, TypeError, AttributeError) as exc:
        raise HairMotionExportError(f'{label} must be a valid UUID.') from exc


def _number(value, limits, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not limits[0] <= value <= limits[1]):
        raise HairMotionExportError(f'{label} must be a finite number in {limits}.')
    return float(value)


def _json_copy(value, label):
    """Reject opaque non-JSON/nonfinite data instead of silently coercing it."""
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise HairMotionExportError(f'{label} contains a nonfinite number.')
        return value
    if isinstance(value, (list, tuple)):
        return [_json_copy(item, label) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json_copy(item, label) for key, item in value.items()}
    raise HairMotionExportError(f'{label} must contain only JSON values and string keys.')


def _vector(value, size, label):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise HairMotionExportError(f'{label} needs exactly {size} coordinates.')
    return [_number(number, (-math.inf, math.inf), label) for number in value]


def _matrix(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise HairMotionExportError(f'{label} needs a 4 by 4 matrix.')
    result = [_vector(row, 4, label) for row in value]
    if result[3] != [0.0, 0.0, 0.0, 1.0]:
        raise HairMotionExportError(f'{label} must be an affine local matrix.')
    return result


def _rest(values, count, label):
    if not isinstance(values, list) or len(values) != count:
        raise HairMotionExportError(f'{label} must cover every ordered bone.')
    result = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise HairMotionExportError(f'{label} has an incomplete bone record.')
        item = _json_copy(value, label)
        for key in ('head', 'tail'):
            item[key] = _vector(item.get(key), 3, label)
        item['matrix'] = _matrix(item.get('matrix'), label)
        if item['head'] == item['tail']:
            raise HairMotionExportError(f'{label} contains a zero-length bone.')
        if type(item.get('parent_index')) is not int or item['parent_index'] != index - 1:
            raise HairMotionExportError(f'{label} must retain ordered chain parenting.')
        for key in ('connected', 'deform', 'inherit_rotation', 'local_location'):
            if type(item.get(key)) is not bool:
                raise HairMotionExportError(f'{label} needs explicit {key}.')
        if not item['deform'] or item.get('inherit_scale') not in {
                'FULL', 'FIX_SHEAR', 'ALIGNED', 'AVERAGE', 'NONE', 'NONE_LEGACY'}:
            raise HairMotionExportError(f'{label} has an unsupported deformation/rest contract.')
        result.append(item)
    return result


def _layers(strand):
    vertices, layers = strand.get('vertices'), strand.get('layers')
    if (not isinstance(vertices, list) or not vertices
            or any(type(index) is not int or index < 0 for index in vertices)
            or len(vertices) != len(set(vertices)) or not isinstance(layers, list) or len(layers) < 2):
        raise HairMotionExportError('A strand needs complete unique source vertices and root-to-tip layers.')
    flat = []
    for layer in layers:
        if not isinstance(layer, list) or not layer or any(type(index) is not int for index in layer):
            raise HairMotionExportError('Every source layer must be nonempty and explicit.')
        flat.extend(layer)
    if len(flat) != len(set(flat)) or set(flat) != set(vertices):
        raise HairMotionExportError('Strand layers must cover its source vertices exactly once.')


def _registry(data):
    if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1
            or data.get('axis') != 'X' or not isinstance(data.get('strands'), list) or not data['strands']):
        raise HairMotionExportError('Use the current validated X-axis Hair strand registry.')
    source_uid = _uid(data.get('source_uid'), 'Hair source UID')
    if data['source_uid'] != source_uid:
        raise HairMotionExportError('The Hair source UID must retain its canonical saved representation.')
    _hash(data.get('topology'), 'Hair topology')
    by_id, orders, native_bones = {}, set(), set()
    namespace = uuid.UUID(source_uid)
    for strand in data['strands']:
        if not isinstance(strand, dict):
            raise HairMotionExportError('A Hair strand record is incomplete.')
        signature = _text(strand.get('signature'), 'Chain signature')
        strand_id = uuid.uuid5(namespace, 'strand:' + signature).hex
        chain_id = uuid.uuid5(namespace, 'chain:' + signature).hex
        if (strand.get('strand_id') != strand_id or strand.get('chain_id') != chain_id
                or strand_id in by_id or type(strand.get('order')) is not int
                or strand['order'] < 0 or strand['order'] in orders):
            raise HairMotionExportError('Strand/chain identities or order are invalid or duplicated.')
        bones = strand.get('bones')
        if not isinstance(bones, list) or not bones:
            raise HairMotionExportError('Each strand needs an explicit ordered native bone path.')
        for bone in bones:
            _text(bone, 'Native bone name')
            if bone in native_bones:
                raise HairMotionExportError('Independent strands cannot overlap native bones.')
            native_bones.add(bone)
        _rest(strand.get('rest'), len(bones), 'Source Rest')
        _layers(strand)
        if not isinstance(strand.get('side'), str) or not isinstance(strand.get('pair_proof'), str):
            raise HairMotionExportError('The strand mirror proof and side must be text.')
        if strand['side'] not in {'L', 'R', 'C', 'U'} or strand['pair_proof'] not in {
                'MIRROR', 'TOPOLOGY', 'GEOMETRY', 'GRAPH', 'MANUAL', 'NONE'}:
            raise HairMotionExportError('The strand has an unsupported mirror proof or side.')
        by_id[strand_id] = strand
        orders.add(strand['order'])
    for strand in data['strands']:
        if strand['pair_proof'] == 'NONE':
            if strand['side'] != 'U' or strand.get('pair_id') is not None or strand.get('mirror_id') is not None:
                raise HairMotionExportError('An unpaired strand cannot carry a partial mirror identity.')
            continue
        mirror_id = strand.get('mirror_id')
        if not isinstance(mirror_id, str):
            raise HairMotionExportError('A paired strand needs an explicit partner identity.')
        partner = by_id.get(mirror_id)
        if partner is None:
            raise HairMotionExportError('A mirror partner is missing from the registry.')
        pair_id = uuid.uuid5(namespace, 'pair:' + ':'.join(sorted(
            (strand['strand_id'], partner['strand_id'])))).hex
        if (partner.get('mirror_id') != strand['strand_id'] or partner['pair_proof'] != strand['pair_proof']
                or strand.get('pair_id') != pair_id or partner.get('pair_id') != pair_id
                or len(strand['bones']) != len(partner['bones'])
                or [len(layer) for layer in strand['layers']] != [len(layer) for layer in partner['layers']]
                or (strand is partner and strand['side'] != 'C')
                or (strand is not partner and {strand['side'], partner['side']} != {'L', 'R'})):
            raise HairMotionExportError('Mirror pairs must be exact, reciprocal and layer/bone compatible.')
        if strand['pair_proof'] == 'MIRROR' and (strand['vertices'] != partner['vertices']
                                               or strand['layers'] != partner['layers']):
            raise HairMotionExportError('Virtual Mirror provenance must retain identical source layers.')
        if strand['pair_proof'] in MAPPED_PROOFS:
            mapping = _vertex_map(strand)
            reverse = _vertex_map(partner)
            if (set(mapping) != set(strand['vertices']) or set(mapping.values()) != set(partner['vertices'])
                    or any(reverse.get(value) != key for key, value in mapping.items())
                    or any({mapping[index] for index in layer} != set(other)
                           for layer, other in zip(strand['layers'], partner['layers']))):
                raise HairMotionExportError('Topology pair proof must cover every source layer reciprocally.')
        if strand['pair_proof'] in BOUNDARY_PROOFS:
            boundary = _mapped_pairs(strand.get('boundary_map'), 'Boundary')
            reverse = _mapped_pairs(partner.get('boundary_map'), 'Boundary')
            if len(boundary) != len(reverse) or any(reverse.get(value) != key for key, value in boundary.items()):
                raise HairMotionExportError('Boundary proof correspondence must remain reciprocal.')
    return data


def _vertex_map(strand):
    return _mapped_pairs(strand.get('vertex_map'), 'Topology')


def _mapped_pairs(raw, label):
    if (not isinstance(raw, list) or any(not isinstance(pair, list) or len(pair) != 2
            or any(type(index) is not int or index < 0 for index in pair) for pair in raw)):
        raise HairMotionExportError(f'{label} pairs need an explicit one-to-one vertex map.')
    result = dict(raw)
    if len(result) != len(raw) or len(set(result.values())) != len(raw):
        raise HairMotionExportError(f'A {label.lower()} pair contains duplicate vertex correspondence.')
    return result


def _profile(value):
    if (not isinstance(value, dict) or not isinstance(value.get('group'), str)
            or value['group'] not in GROUPS):
        raise HairMotionExportError('Each configured strand needs its explicit semantic group.')
    if type(value.get('sync_mirror', True)) is not bool:
        raise HairMotionExportError('Mirror synchronization must be a boolean.')
    effective = {key: _number(value.get(key), RANGES[key], key)
                 for key in ('recovery', 'damping', 'gravity', 'stretch')}
    raw = value.get('depth')
    keys = {'position', 'recovery', 'damping', 'mass', 'gravity'}
    if not isinstance(raw, list) or not 2 <= len(raw) <= 24:
        raise HairMotionExportError('Depth needs 2 to 24 normalized controls including root and tip.')
    depth = []
    for knot in raw:
        if not isinstance(knot, dict) or set(knot) != keys:
            raise HairMotionExportError('A depth control is incomplete or includes unsupported fields.')
        position = _number(knot['position'], (0.0, 1.0), 'Depth position')
        if depth and position <= depth[-1]['position']:
            raise HairMotionExportError('Depth controls must increase strictly from root to tip.')
        depth.append({'position': position, **{key: _number(knot[key], RANGES[key], key)
                                               for key in ('recovery', 'damping', 'mass', 'gravity')}})
    if depth[0]['position'] != 0.0 or depth[-1]['position'] != 1.0:
        raise HairMotionExportError('Depth controls must retain root 0 and tip 1.')
    result = {'group': value['group'], 'effective': effective, 'depth': depth,
              'sync_mirror': value.get('sync_mirror', True)}
    for key in ('backend_overrides', 'raw_config'):
        if key in value:
            result[key] = _json_copy(value[key], key)
    allowed = {'group', 'sync_mirror', 'depth', 'backend_overrides', 'raw_config'} | set(effective)
    if set(value) - allowed:
        raise HairMotionExportError('Unknown motion fields must be placed in explicit raw_config.')
    return result


def _profiles(profiles, registry):
    expected = {strand['strand_id'] for strand in registry['strands']}
    if not isinstance(profiles, dict) or set(profiles) != expected:
        raise HairMotionExportError('Configured motion settings must cover the exact current strand inventory.')
    result = {key: _profile(value) for key, value in profiles.items()}
    for strand in registry['strands']:
        mirror = strand.get('mirror_id')
        if mirror is None or mirror == strand['strand_id']:
            continue
        own, other = result[strand['strand_id']], result[mirror]
        if own['sync_mirror'] != other['sync_mirror'] or own['sync_mirror'] and own != other:
            raise HairMotionExportError('Synchronized mirror settings conflict; reconcile them explicitly.')
    return result


def _units(values):
    if not isinstance(values, dict):
        raise HairMotionExportError('Supply the actual source/export unit contract.')
    result = _json_copy(values, 'Unit contract')
    for key in ('source_meters_per_unit', 'export_meters_per_unit'):
        result[key] = _number(result.get(key), (0.0, math.inf), key)
        if result[key] <= 0.0:
            raise HairMotionExportError('Length units must be strictly positive.')
    if 'fbx_global_scale' in result:
        result['fbx_global_scale'] = _number(result['fbx_global_scale'], (0.0, math.inf), 'FBX global scale')
        if result['fbx_global_scale'] <= 0.0:
            raise HairMotionExportError('FBX global scale must be strictly positive.')
    if 'source_to_export' in result:
        result['source_to_export'] = _matrix(result['source_to_export'], 'Source-to-export matrix')
    return result


def _mapping(values):
    if not isinstance(values, dict) or not values:
        raise HairMotionExportError('Supply explicit native-to-exported bone bindings.')
    result, destinations = {}, set()
    for source_name, value in values.items():
        _text(source_name, 'Binding source bone')
        if isinstance(value, str):
            item = {'name': _text(value, 'Exported bone name')}
        elif isinstance(value, dict) and set(value) <= {'name', 'rest'}:
            item = {'name': _text(value.get('name'), 'Exported bone name')}
            if 'rest' in value:
                item['rest'] = _json_copy(value['rest'], 'Actual exported Rest')
        else:
            raise HairMotionExportError('A bone binding needs an explicit exported name and optional actual rest.')
        if item['name'] in destinations:
            raise HairMotionExportError('Exported bone bindings must be one-to-one, including unused entries.')
        result[source_name] = item
        destinations.add(item['name'])
    return result


def _tool_version():
    package = sys.modules.get(__package__)
    version = getattr(package, 'bl_info', {}).get('version') if package else None
    return '.'.join(str(part) for part in version) if version else EXPORTER_VERSION


def build_payload(source, registry, profiles, bone_mapping, asset_id, fbx_hash, units, *, tool_version=None):
    """Build detached data; ``{}`` means no motion settings, hence no sidecar.

    ``registry`` is a strict reader or its already-validated detached result.
    ``profiles`` is the effective-all mapping, not group defaults/raw records.
    ``bone_mapping`` is the worker's explicit final source-name mapping. Every
    strand must be retained completely; missing bones are errors, never guesses.
    """
    if profiles is None or profiles == {}:
        return {}
    if hasattr(registry, 'read'):
        registry = registry.read(source, validate=True)
    registry = _registry(copy.deepcopy(registry))
    settings = _profiles(profiles, registry)
    mapping = _mapping(bone_mapping)
    name = source.get('name') if isinstance(source, dict) else getattr(source, 'name', None)
    source_name = _text(name, 'Source object name')
    strands = []
    for strand in sorted(registry['strands'], key=lambda item: (item['order'], item['strand_id'])):
        if any(bone not in mapping for bone in strand['bones']):
            raise HairMotionExportError('An owned strand bone is absent from the explicit FBX bone mapping.')
        item = {key: copy.deepcopy(strand[key]) for key in ('strand_id', 'chain_id', 'signature', 'order',
                'pair_id', 'mirror_id', 'side', 'pair_proof', 'vertices', 'layers')}
        item.update(source_bones=list(strand['bones']),
                    exported_bones=[mapping[bone]['name'] for bone in strand['bones']],
                    source_rest=_rest(strand['rest'], len(strand['bones']), 'Source Rest'),
                    **settings[strand['strand_id']])
        if strand['pair_proof'] in MAPPED_PROOFS:
            item['vertex_map'] = copy.deepcopy(strand['vertex_map'])
        if strand['pair_proof'] in BOUNDARY_PROOFS:
            # The strict source reader proves incident edges/faces. This is
            # detached provenance outside the core strand's vertex domain.
            item['boundary_map'] = copy.deepcopy(strand['boundary_map'])
        if 'anchor' in strand:
            item['source_anchor'] = _json_copy(strand['anchor'], 'Source anchor')
        rests = [mapping[bone].get('rest') for bone in strand['bones']]
        if any(rest is not None for rest in rests):
            if any(rest is None for rest in rests):
                raise HairMotionExportError('Actual exported Rest must cover the complete ordered strand.')
            item['exported_rest'] = _rest(rests, len(rests), 'Exported Rest')
        strands.append(item)
    payload = {'schema': SCHEMA, 'schema_version': SCHEMA_VERSION,
        'tool': {'name': 'Character Designer', 'version': _text(tool_version or _tool_version(), 'Tool version'),
                 'exporter_version': EXPORTER_VERSION},
        'source_uid': registry['source_uid'], 'source_name': source_name,
        'asset_id': _text(asset_id, 'Export asset ID'), 'fbx_sha256': _hash(fbx_hash, 'FBX hash'),
        'registry': {'version': 1, 'topology': _hash(registry['topology'], 'Hair topology'), 'axis': 'X'},
        'units': _units(units),
        'coordinates': {'source': 'BLENDER_Z_UP_RIGHT_HANDED', 'consumer': 'UNITY_Y_UP_LEFT_HANDED',
                        'fbx_axis_forward': '-Z', 'fbx_axis_up': 'Y',
                        'source_rest_space': 'source_armature_local', 'source_rest_unit': 'source_length_unit',
                        'exported_rest_space': 'export_armature_local', 'exported_rest_unit': 'export_length_unit'},
        'conversion': dict(CONVERSION), 'parameter_contract': copy.deepcopy(PARAMETERS),
        'application': {'baseline_policy': 'inherit_existing_native', 'require_explicit_apply': True,
                        'component_layout': 'independent_per_strand', 'conversion_validation': 'pending'},
        'simulation_baked': False, 'strands': strands}
    validate_payload(payload)
    return payload


def validate_payload(payload):
    """Validate an untrusted loaded sidecar without touching either application."""
    if (not isinstance(payload, dict) or payload.get('schema') != SCHEMA
            or type(payload.get('schema_version')) is not int or payload['schema_version'] != SCHEMA_VERSION
            or payload.get('simulation_baked') is not False or payload.get('conversion') != CONVERSION
            or payload.get('parameter_contract') != PARAMETERS):
        raise HairMotionExportError('Unsupported Hair motion sidecar or simulation/conversion contract.')
    expected_application = {'baseline_policy': 'inherit_existing_native', 'require_explicit_apply': True,
                            'component_layout': 'independent_per_strand', 'conversion_validation': 'pending'}
    if payload.get('application') != expected_application:
        raise HairMotionExportError('Hair sidecars require explicit application over the existing native baseline.')
    for key in ('source_name', 'asset_id'):
        _text(payload.get(key), key)
    _hash(payload.get('fbx_sha256'), 'FBX hash')
    tool = payload.get('tool')
    if not isinstance(tool, dict) or tool.get('name') != 'Character Designer':
        raise HairMotionExportError('A producer identity is required.')
    for key in ('version', 'exporter_version'):
        _text(tool.get(key), key)
    _units(payload.get('units'))
    coordinates = payload.get('coordinates')
    if not isinstance(coordinates, dict) or coordinates != {
            'source': 'BLENDER_Z_UP_RIGHT_HANDED', 'consumer': 'UNITY_Y_UP_LEFT_HANDED',
            'fbx_axis_forward': '-Z', 'fbx_axis_up': 'Y',
            'source_rest_space': 'source_armature_local', 'source_rest_unit': 'source_length_unit',
            'exported_rest_space': 'export_armature_local', 'exported_rest_unit': 'export_length_unit'}:
        raise HairMotionExportError('The sidecar must declare its source Rest and FBX coordinate contract.')
    saved = payload.get('registry')
    if not isinstance(saved, dict) or not isinstance(payload.get('strands'), list):
        raise HairMotionExportError('The strand registry/bindings are incomplete.')
    registry = {'version': saved.get('version'), 'topology': saved.get('topology'),
                'axis': saved.get('axis'), 'source_uid': payload.get('source_uid'), 'strands': []}
    profiles, mapping = {}, {}
    for strand in payload['strands']:
        if not isinstance(strand, dict):
            raise HairMotionExportError('A sidecar strand is malformed.')
        item = {key: copy.deepcopy(strand.get(key)) for key in ('strand_id', 'chain_id', 'signature', 'order',
                'pair_id', 'mirror_id', 'side', 'pair_proof', 'vertices', 'layers', 'vertex_map', 'boundary_map')}
        item['bones'], item['rest'] = strand.get('source_bones'), strand.get('source_rest')
        registry['strands'].append(item)
        effective = strand.get('effective')
        if not isinstance(effective, dict) or set(effective) != {'recovery', 'damping', 'gravity', 'stretch'}:
            raise HairMotionExportError('The sidecar needs its complete effective semantic settings.')
        profile = {**effective, 'group': strand.get('group'), 'depth': strand.get('depth'),
                   'sync_mirror': strand.get('sync_mirror')}
        for key in ('backend_overrides', 'raw_config'):
            if key in strand:
                profile[key] = strand[key]
        profiles[strand.get('strand_id')] = profile
        source_bones, exported = strand.get('source_bones'), strand.get('exported_bones')
        if (not isinstance(source_bones, list) or not isinstance(exported, list)
                or len(exported) != len(source_bones)):
            raise HairMotionExportError('The exported bone path must bind every source bone in order.')
        for source_name, exported_name in zip(source_bones, exported):
            _text(source_name, 'Source bone binding')
            if source_name in mapping:
                raise HairMotionExportError('The sidecar repeats a native bone binding.')
            mapping[source_name] = exported_name
        if 'exported_rest' in strand:
            _rest(strand['exported_rest'], len(source_bones), 'Exported Rest')
    _registry(registry)
    _profiles(profiles, registry)
    _mapping(mapping)
    _json_copy(payload, 'Sidecar')
    return payload


def _bytes(payload):
    validate_payload(payload)
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HairMotionExportError('The sidecar contains a duplicate JSON object key.')
        result[key] = value
    return result


def verify_read(path, expected=None, *, fbx_path=None):
    """Read/validate; optionally bind to exact expected payload and actual FBX."""
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, HairMotionExportError):
            raise
        raise HairMotionExportError('The Hair motion sidecar cannot be read.') from exc
    validate_payload(payload)
    if expected is not None and _bytes(payload) != _bytes(expected):
        raise HairMotionExportError('The saved sidecar differs from the complete expected payload.')
    if fbx_path is not None:
        digest = hashlib.sha256()
        try:
            with Path(fbx_path).open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
        except OSError as exc:
            raise HairMotionExportError('The FBX binding cannot be verified.') from exc
        if digest.hexdigest() != payload['fbx_sha256']:
            raise HairMotionExportError('The sidecar hash does not match the actual FBX.')
    return payload


def write_atomic(path, payload):
    """Replace only after a complete, read-verified same-directory write.

    Empty/invalid payloads fail before opening any output. Parent directories are
    never created here; publication scope and package rollback belong to caller.
    """
    encoded = _bytes(payload)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.' + path.name + '.', suffix='.tmp',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        verify_read(temporary, payload)
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise HairMotionExportError('The Hair motion sidecar could not be published atomically.') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
