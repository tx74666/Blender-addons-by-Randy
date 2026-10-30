"""Owned, unbaked Hair preview using the verified external Wiggle 2 2.2.4.

Mapping v2 uses the upstream 2.2.4 equations: stiff=800*recovery**2,
damp=20*damping, native gravity=meters_per_second_squared/(|scene.gravity|
*scene.unit_settings.scale_length), native stretch and relative chain mass.
The native solver stores world-space endpoints, so Object scale is not applied
to gravity again. These coefficients are preview controls, not material units.

Only registry-owned selected Hair bones are changed/restored. The shared runtime
owns native fields and list membership; disjoint Dress leases may keep running.
There is no bake, key creation, frame rewind, global freeze or playback cancel.
Save/history/load/render end this transient lease; none reconnect automatically.
"""

import math
import hashlib
import json
import struct
from array import array
import sys
import time

import bpy
from bpy.app.handlers import persistent

from . import hair_bones_rig as hair
from . import hair_strand_registry as strands
from . import wiggle2_runtime as runtime
from .hair_wiggle_adapter import HairWiggleError

BACKEND_VERSION = '2.2.4'
MAPPING_VERSION = 2
_SESSION = None
_LAST_REASON = ''
_STOPPING = False
_GUARDS_REGISTERED = False
_TIMER_RUNNING = False
_RECOVERY_KEY = 'character_designer_hair_wiggle2_preview_v1'
_HISTORY_PENDING = False
_LAST_SOURCE_POINTER = None
_LIFECYCLE_LISTS = ('save_pre', 'undo_pre', 'redo_pre', 'load_pre', 'render_pre')
_ANIMATION_SKIP = frozenset({'rna_type', 'users', 'tag', 'is_updated', 'is_updated_data',
    'is_updated_transform', 'is_valid', 'is_evaluated', 'session_uid'})
_ANIMATION_MEMO_TYPES = frozenset({'FCurve', 'ActionChannelbag', 'ActionGroup'})
_ANIMATION_POINT_TYPES = frozenset({'Keyframe', 'FCurveSample'})
_ANIMATION_ENUM_FIELDS = frozenset({'interpolation', 'easing', 'type', 'handle_left_type', 'handle_right_type'})
_ANIMATION_MAX_BUFFER_ITEMS = 1000000
_ANIMATION_MAX_MEMO = 4096
_ARP_INERT_SOURCE_SHA256 = '961e64e26f48d8dcb234959f043343a75b647a8fd68beb37d8b081ac35e0bb61'
_ARP_INERT_PATHS = ('arp_layers_animated', '["arp_layers_animated"]', "['arp_layers_animated']")
_ARP_INERT_CODE_FIELDS = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount',
    'co_nlocals', 'co_stacksize', 'co_flags', 'co_code', 'co_consts', 'co_names',
    'co_varnames', 'co_freevars', 'co_cellvars', 'co_name', 'co_qualname', 'co_exceptiontable')
_ARP_INERT_CERTIFICATE = None


class _AnimationHash:
    """Complete typed RNA content, streamed without a Python point snapshot.

    Numeric foreach buffers contain exact promoted float64/int32/bool values,
    capped below eight MiB. Larger collections fall back to constant-space
    scalar reads. The digest encoding is identical for both paths. Only small
    repeated FCurve/container definitions are memoized, within this one pass.
    """
    def __init__(self, *, force_scalar=False):
        self.hash = hashlib.sha256()
        self.plans = {}
        self.memo = {}
        self.active = set()
        self.no_custom = set()
        self.no_bulk = set()
        self.enum_tables = {}
        self.force_scalar = force_scalar
        self.stats = dict(bulk_fields=0, numeric_values=0, max_buffer_bytes=0,
                          memo_hits=0, rna_nodes=0, enum_bulk_fields=0,
                          enum_values=0, enum_scalar_validation_points=0)

    def put(self, value):
        if value is None:
            self.hash.update(b'N')
        elif type(value) is bool:
            self.hash.update(b'T' if value else b'F')
        elif type(value) is int:
            self.hash.update(b'I')
            self.put(str(value))
        elif type(value) is float:
            self.hash.update(b'D' + struct.pack('!d', value))
        elif isinstance(value, str):
            self.hash.update(b'S' + struct.pack('!Q', len(value)))
            for offset in range(0, len(value), 4096):
                payload = value[offset:offset + 4096].encode('utf8', 'surrogatepass')
                self.hash.update(struct.pack('!Q', len(payload)))
                self.hash.update(payload)
        elif isinstance(value, (bytes, bytearray, memoryview)):
            self.hash.update(b'B' + struct.pack('!Q', len(value)))
            self.hash.update(value)
        elif isinstance(value, bpy.types.ID):
            self.put(('ID', value.bl_rna.identifier, value.as_pointer(), value.name_full,
                      value.library.as_pointer() if value.library else None))
        elif hasattr(value, 'keys'):
            self.hash.update(b'{')
            for key in sorted(value.keys()):
                self.put(key)
                self.put(value[key])
            self.hash.update(b'}')
        elif isinstance(value, (set, frozenset)):
            self.put(tuple(sorted(value)))
        else:
            code = getattr(value, 'typecode', None)
            if code is not None:
                self.put(('ARRAY_TYPE', code))
            self.hash.update(b'[')
            try:
                for item in value:
                    self.put(item)
            except TypeError as error:
                raise HairWiggleError('Unsupported Action proof value: ' + type(value).__name__) from error
            self.hash.update(b']')

    def plan(self, owner):
        descriptor = owner.bl_rna
        key = descriptor.as_pointer()
        cached = self.plans.get(key)
        if cached is None:
            plan = tuple(prop for prop in descriptor.properties if prop.identifier not in _ANIMATION_SKIP)
            # Keep the exact descriptor alive; no pointer reuse in this pass.
            cached = descriptor, plan
            self.plans[key] = cached
        return cached[1]

    def custom(self, owner):
        typename = owner.bl_rna.identifier
        if typename in self.no_custom:
            self.put(('NO_CUSTOM_PROPERTIES', typename))
            return
        try:
            keys = owner.keys()
        except TypeError as error:
            if str(error) != "bpy_struct.keys(): this type doesn't support IDProperties":
                raise
            self.no_custom.add(typename)
            self.put(('NO_CUSTOM_PROPERTIES', typename))
            return
        self.hash.update(b'{')
        for key in sorted(keys):
            self.put(key)
            self.put(owner[key])
        self.hash.update(b'}')

    def property(self, owner, prop, depth):
        name = prop.identifier
        if (prop.type == 'POINTER' and hasattr(owner, 'is_property_set')
                and not owner.is_property_set(name)):
            if isinstance(owner, bpy.types.PropertyGroup):
                self.put(('UNSET_POINTER', name))
                return
            if getattr(prop, 'is_runtime', False):
                # Unknown runtime PG targets may even have been freed by a
                # disabled add-on. Do not dereference fixed_type or materialize
                # Action storage just to inspect its defaults; refuse explicitly.
                _fail('An unset registered animation pointer needs a separate read-only proof: '
                      + owner.bl_rna.identifier + '.' + name)
        try:
            value = getattr(owner, name)
        except (AttributeError, RuntimeError) as error:
            self.put(('UNAVAILABLE', owner.bl_rna.identifier, name, type(error).__name__, str(error)))
            return
        if prop.type == 'COLLECTION':
            self.collection(value, depth + 1)
        elif prop.type == 'POINTER' and value is not None:
            if isinstance(value, bpy.types.ID):
                self.put(value)
            else:
                # Drivers and AssetMetaData are owned non-ID RNA, not merely
                # labels. Expand their complete properties; active graph back
                # edges are exact references inside rna(), never recursive copies.
                self.rna(value, depth + 1)
        else:
            self.put(value)

    def rna(self, owner, depth=0):
        if owner is None:
            self.put(None)
            return
        if depth > 12:
            _fail('Animation data exceeds the supported rollback proof depth.')
        typename = owner.bl_rna.identifier
        key = typename, owner.as_pointer()
        digest = self.memo.get(key) if typename in _ANIMATION_MEMO_TYPES else None
        if digest is not None:
            self.stats['memo_hits'] += 1
            self.put(('RNA_CONTENT', typename, digest))
            return
        if key in self.active:
            self.put(('RNA_BACKREFERENCE', typename, owner.as_pointer()))
            return
        parent_hash = self.hash
        self.hash = hashlib.sha256()
        self.active.add(key)
        self.stats['rna_nodes'] += 1
        try:
            self.put(('RNA_DEFINITION', typename))
            for prop in self.plan(owner):
                self.put((prop.identifier, prop.type, bool(getattr(prop, 'is_array', False)),
                          tuple(getattr(prop, 'array_dimensions', ()))))
                self.property(owner, prop, depth)
            self.custom(owner)
            digest = self.hash.digest()
        finally:
            self.active.remove(key)
            self.hash = parent_hash
        if typename in _ANIMATION_MEMO_TYPES and len(self.memo) < _ANIMATION_MAX_MEMO:
            self.memo[key] = digest
        self.put(('RNA_CONTENT', typename, digest))

    def numeric(self, collection, prop, typename):
        count = len(collection)
        width = getattr(prop, 'array_length', 0) if getattr(prop, 'is_array', False) else 1
        if width < 1:
            return False
        kind, packed = {'FLOAT': ('d', '!d'), 'INT': ('i', '!i'), 'BOOLEAN': ('b', '!b')}[prop.type]
        self.put(('NUMERIC_ARRAY', prop.type, count, width,
                  tuple(getattr(prop, 'array_dimensions', ()))))
        bulk_key = typename, prop.identifier
        if (not self.force_scalar and hasattr(collection, 'foreach_get') and count * width <= _ANIMATION_MAX_BUFFER_ITEMS
                and bulk_key not in self.no_bulk):
            values = array(kind, [0]) * (count * width)
            try:
                collection.foreach_get(prop.identifier, values)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                self.no_bulk.add(bulk_key)
            else:
                self.stats['bulk_fields'] += 1
                self.stats['numeric_values'] += len(values)
                self.stats['max_buffer_bytes'] = max(self.stats['max_buffer_bytes'], len(values) * values.itemsize)
                if sys.byteorder == 'little' and values.itemsize > 1:
                    values.byteswap()
                self.hash.update(memoryview(values).cast('B'))
                return True
            finally:
                del values
        for owner in collection:
            value = getattr(owner, prop.identifier)
            if getattr(prop, 'is_array', False):
                for number in self.flatten(value):
                    self.hash.update(struct.pack(packed, number))
            else:
                self.hash.update(struct.pack(packed, value))
        return True

    def enumeration(self, collection, prop, typename):
        """Lossless identifier/code table plus every ordered native int32 value.

        The two scalar checks authenticate the native API encoding, not point
        contents: all codes are still read, schema-validated and hashed. Any
        unsupported/mismatching bulk getter takes the identical scalar encoding.
        """
        if (typename != 'Keyframe' or prop.identifier not in _ANIMATION_ENUM_FIELDS
                or getattr(prop, 'is_array', False) or getattr(prop, 'is_enum_flag', False)):
            return False
        key = typename, prop.identifier
        cached = self.enum_tables.get(key)
        if cached is None:
            table = tuple((item.identifier, item.value) for item in prop.enum_items if item.identifier)
            if (not table or len({name for name, code in table}) != len(table)
                    or len({code for name, code in table}) != len(table)
                    or any(type(code) is not int or not -(2 ** 31) <= code < 2 ** 31 for name, code in table)):
                _fail('The native Keyframe enum schema is not an exact one-to-one int32 map: ' + prop.identifier)
            cached = table, dict(table), frozenset(code for name, code in table)
            self.enum_tables[key] = cached
        table, codes, allowed = cached
        count = len(collection)
        self.put(('ENUM_INT32_ARRAY', typename, prop.identifier, count, table))
        if (not self.force_scalar and hasattr(collection, 'foreach_get')
                and count <= _ANIMATION_MAX_BUFFER_ITEMS and key not in self.no_bulk):
            values = array('i', [0]) * count
            try:
                try:
                    collection.foreach_get(prop.identifier, values)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    self.no_bulk.add(key)
                else:
                    # This is complete validation, using O(1) additional space.
                    if any(code not in allowed for code in values):
                        _fail('Unknown native Keyframe enum value: ' + prop.identifier)
                    checks = (0, count - 1) if count > 1 else (0,)
                    matched = True
                    for index in checks:
                        identifier = getattr(collection[index], prop.identifier)
                        if identifier not in codes:
                            _fail('Unknown native Keyframe enum identifier: ' + prop.identifier)
                        if codes[identifier] != values[index]:
                            matched = False
                            break
                    if matched:
                        self.stats['enum_bulk_fields'] += 1
                        self.stats['enum_values'] += count
                        self.stats['enum_scalar_validation_points'] += len(checks)
                        self.stats['max_buffer_bytes'] = max(self.stats['max_buffer_bytes'], len(values) * values.itemsize)
                        if sys.byteorder == 'little':
                            values.byteswap()
                        self.hash.update(memoryview(values).cast('B'))
                        return True
                    self.no_bulk.add(key)
            finally:
                del values
        for owner in collection:
            identifier = getattr(owner, prop.identifier)
            if identifier not in codes:
                _fail('Unknown native Keyframe enum identifier: ' + prop.identifier)
            self.hash.update(struct.pack('!i', codes[identifier]))
        return True

    @staticmethod
    def flatten(value):
        for item in value:
            if isinstance(item, (int, float, bool)):
                yield item
            else:
                yield from _AnimationHash.flatten(item)

    def collection(self, collection, depth):
        count = len(collection)
        self.put(('COLLECTION', count))
        if not count:
            return
        first = next(iter(collection))
        if isinstance(first, bpy.types.ID):
            # Association inventories contain ID bindings, not permission to
            # traverse another Object/Action datablock's contents.
            for owner in collection:
                self.put(owner)
            return
        typename = first.bl_rna.identifier
        if typename not in _ANIMATION_POINT_TYPES or count <= 8:
            for owner in collection:
                self.rna(owner, depth)
            return
        # Homogeneous native point collections only. Mixed modifier subclasses
        # always take the complete per-owner schema path above.
        self.put(('TRANSPOSED_POINTS', typename))
        for prop in self.plan(first):
            self.put((prop.identifier, prop.type, bool(getattr(prop, 'is_array', False)),
                      tuple(getattr(prop, 'array_dimensions', ()))))
            if prop.type in {'FLOAT', 'INT', 'BOOLEAN'} and self.numeric(collection, prop, typename):
                continue
            if prop.type == 'ENUM' and self.enumeration(collection, prop, typename):
                continue
            for owner in collection:
                self.property(owner, prop, depth)
        # Point RNA normally cannot carry custom properties. Cache only that
        # native type capability, never an individual point's values.
        self.custom(first)
        if typename in self.no_custom:
            self.put(('NO_CUSTOM_COLLECTION', typename, count))
        else:
            iterator = iter(collection)
            next(iterator)
            for owner in iterator:
                self.custom(owner)


def _animation_proof(*, include_actions=True, include_associations=True, owners=None, diagnostics=None,
                     force_scalar=False):
    """Exact bounded fingerprint of all Actions and captured existing owners.

    The token retains only ID references for the small owner inventory, never
    keyframes, handles or expanded RNA. Controls-only checks may explicitly
    exclude associations; normal Hair Start/Stop always use the complete scope.
    """
    writer = _AnimationHash(force_scalar=force_scalar)
    writer.put(('HAIR_ANIMATION_STREAM', 1, bool(include_actions), bool(include_associations)))
    action_refs = tuple(bpy.data.actions) if include_actions else ()
    action_count = len(action_refs)
    writer.put(('ACTIONS', action_count))
    if include_actions:
        for action in action_refs:
            writer.put(action)
            writer.rna(action)
    owner_refs = ()
    if include_associations:
        if owners is None:
            scene = bpy.context.scene
            owner_refs = (scene,) + tuple(scene.objects) + tuple(
                ob.data.shape_keys for ob in scene.objects if ob.type == 'MESH' and ob.data.shape_keys)
        else:
            owner_refs = tuple(owners)
        writer.put(('ASSOCIATIONS', len(owner_refs)))
        for owner in owner_refs:
            writer.put(owner)
            writer.rna(owner.animation_data if owner.animation_data else None)
    if diagnostics is not None:
        diagnostics.update(writer.stats)
    return {'schema': 1, 'sha256': writer.hash.hexdigest(), 'include_actions': bool(include_actions),
            'include_associations': bool(include_associations), 'action_count': action_count,
            'owner_count': len(owner_refs), 'owner_refs': owner_refs, 'action_refs': action_refs}


def _animation_same(proof):
    try:
        if proof['action_refs']:
            # Local and multiple linked Actions may share a name. Authenticate
            # the retained live IDs, without choosing an arbitrary name match.
            current = {(action.bl_rna.identifier, action.as_pointer()): action for action in bpy.data.actions}
            if any(action.as_pointer() == 0
                   or current.get((action.bl_rna.identifier, action.as_pointer())) != action
                   for action in proof['action_refs']):
                return False
        return proof == _animation_proof(include_actions=proof['include_actions'],
            include_associations=proof['include_associations'], owners=proof['owner_refs'])
    except (HairWiggleError, ValueError, TypeError, KeyError, AttributeError, ReferenceError, RuntimeError):
        return False


def _old():
    from . import hair_wiggle_adapter
    return hair_wiggle_adapter


def _fail(message):
    raise HairWiggleError(message)


def _backend():
    # One exact file/schema/handler proof for both consumers of the same solver.
    from . import skirt_wiggle
    return skirt_wiggle._backend()


def available(context=None):
    """Read-only cached proof; only explicit dependency actions enable Wiggle."""
    old = _old()
    now = time.monotonic()
    cached = old._AVAILABLE_CACHE
    if (cached is not None and cached.get('mapping_version') == MAPPING_VERSION
            and now - old._AVAILABLE_CACHE_TIME < 1.0):
        return dict(cached)
    try:
        backend = _backend()
        result = {'available': True, 'version': BACKEND_VERSION,
                  'module': backend.__name__, 'reason': '', 'mapping_version': MAPPING_VERSION}
    except (ValueError, RuntimeError, AttributeError, ReferenceError) as error:
        result = {'available': False, 'version': None, 'module': None,
                  'reason': str(error), 'mapping_version': MAPPING_VERSION}
    old._AVAILABLE_CACHE, old._AVAILABLE_CACHE_TIME = result, now
    return dict(result)


def status(context=None):
    session = _SESSION
    return {'active': session is not None,
            'source': (session['source_name'] if 'source_name' in session else session['source'].name) if session else None,
            'source_pointer': session.get('source_pointer') if session else _LAST_SOURCE_POINTER,
            'reason_scope': _LAST_SOURCE_POINTER,
            'recovery_pending': _HISTORY_PENDING,
            'cleanup_pending': bool(session and session.get('lease_released', False)),
            'paused': bool(session and session.get('physics_paused', False)),
            'strand_ids': list(session['ids']) if session else [],
            'started_playback': bool(session and session['started_playback']),
            'reason': _LAST_REASON, 'version': BACKEND_VERSION,
            'mapping_version': MAPPING_VERSION, 'backend': 'WIGGLE2'}


def _encode(value):
    if isinstance(value, bpy.types.ID):
        if not isinstance(value, (bpy.types.Object, bpy.types.Collection)):
            _fail('This native Hair setting has an unsupported ID recovery reference.')
        return {'$id': value.bl_rna.identifier, 'name': value.name_full,
                'library': value.library.filepath if value.library else None}
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        _fail('Non-finite native Hair settings cannot be restored safely.')
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    _fail('Unsupported native Hair recovery value.')


def _decode(value):
    if isinstance(value, dict) and '$id' in value:
        tables = {'Object': bpy.data.objects, 'Collection': bpy.data.collections}
        if value['$id'] not in tables:
            _fail('Unsupported saved Hair native ID reference.')
        candidates = [item for item in tables[value['$id']]
                      if item.name_full == value['name']
                      and (item.library.filepath if item.library else None) == value['library']]
        if value['$id'] == 'Collection':
            candidates += [scene.collection for scene in bpy.data.scenes
                           if scene.collection.name_full == value['name']
                           and (scene.collection.library.filepath if scene.collection.library else None) == value['library']
                           and scene.collection not in candidates]
        if len(candidates) != 1:
            _fail('A saved native Hair ID reference changed; preserve its existing settings.')
        return candidates[0]
    if isinstance(value, dict):
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return tuple(_decode(item) for item in value)
    return value


def _text_hash(value):
    if not isinstance(value, str):
        _fail('Hair preview recovery needs its original saved ownership records.')
    return hashlib.sha256(value.encode('utf8')).hexdigest()


def _write_marker(session):
    lease, source = session['lease'], session['source']
    value = {'version': 1, 'component': 'HAIR', 'source': source.name_full,
        'source_uid': session['registry']['source_uid'], 'rig': session['armature'].name_full,
        'scene': session['scene'].name_full, 'ids': list(session['ids']), 'names': list(session['names']),
        'registry_sha256': _text_hash(session['registry_raw']),
        'rig_record_sha256': _text_hash(session['rig_record']),
        'original': _encode(lease['native_original']), 'groups': _encode(lease['group_original']),
        'scene_original': _encode(lease['scene_original']), 'rig_original': _encode(lease['rig_original']),
        'paused': bool(lease['paused']),
        'expected': _encode({name: {field: runtime._raw(bone, field) for field in lease['fields']}
                             for name, bone in lease['bones'].items()})}
    source[_RECOVERY_KEY] = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    session['marker_raw'] = source[_RECOVERY_KEY]


def preview_state(source):
    """Lightweight display facts; no recovery, backend scan or ownership grant."""
    session = _SESSION
    active = bool(session and session['source'] == source)
    rig = source.get(hair.RIG_KEY)
    marker = source.get(_RECOVERY_KEY)
    flags = False
    try:
        saved = strands.read(source, validate=False)
        flags = bool(saved and rig and any(
            bool(getattr(rig.pose.bones[name], field, rig.pose.bones[name].get(field, False)))
            for item in saved['strands'] for name in item['bones']
            for field in ('wiggle_head', 'wiggle_tail')))
    except (ValueError, TypeError, KeyError, AttributeError, ReferenceError):
        return {'active': active, 'paused': False, 'running': False,
                'stopped_confirmed': False, 'pending': bool(marker), 'pending_recovery': bool(marker),
                'reason': 'Hair ownership needs verification.', 'message': 'Hair ownership needs verification.'}
    if active:
        lease = session['lease']
        try:
            valid = (not session.get('lease_released', False) and all(
                rig.pose.bones.get(name) == lease['bones'][name]
                and all(getattr(lease['bones'][name], field) == value for field, value in values.items())
                for name, values in lease['expected_flags'].items()))
        except (KeyError, AttributeError, ReferenceError):
            valid = False
        paused = bool(valid and session.get('physics_paused', False))
        message = '' if valid else 'Owned Hair native flags need verification.'
        return {'active': True, 'paused': paused, 'running': bool(valid and not paused),
                'stopped_confirmed': paused, 'pending': False, 'pending_recovery': False,
                'reason': message, 'message': message}
    message = ('Saved Hair preview flags need explicit recovery.' if marker
               else 'Unowned Hair simulation flags need review.' if flags else '')
    return {'active': False, 'paused': False, 'running': False,
            'stopped_confirmed': not flags, 'pending': bool(marker), 'pending_recovery': bool(marker),
            'reason': message, 'message': message}


def prepare_recovery(context, source):
    """Authenticate an inert history marker and all produced fields, read-only."""
    if _SESSION is not None and _SESSION['source'] == source:
        return {'source': source, 'needed': False, 'changed': False, 'stopped_confirmed': False, 'reason': ''}
    raw = source.get(_RECOVERY_KEY)
    if raw is None:
        state = preview_state(source)
        if not state['stopped_confirmed']:
            _fail(state['reason'])
        return {'source': source, 'needed': False, 'changed': False, 'stopped_confirmed': True, 'reason': ''}
    backend = _backend()
    fresh = strands.read(source, validate=True)
    rig = source.get(hair.RIG_KEY)
    if (context.scene != bpy.context.scene or context.mode not in {'OBJECT', 'POSE'}
            or source.library or source.override_library or not source.is_editable
            or not isinstance(rig, bpy.types.Object) or rig.library or rig.override_library
            or not rig.is_editable or rig.data.library or context.scene.objects.get(rig.name) != rig):
        _fail('Recover Hair preview only on its exact local source and rig in the active Scene.')
    from . import limb_ik
    if not limb_ik._single_armature_user(rig):
        _fail('Hair recovery needs its original single-user rig ownership proof.')
    try:
        marker = json.loads(raw)
        by_id = {item['strand_id']: item for item in fresh['strands']}
        ids = marker['ids']
        names = tuple(name for key in ids for name in by_id[key]['bones'])
        valid = (marker['version'] == 1 and marker['component'] == 'HAIR'
            and marker['source'] == source.name_full and marker['source_uid'] == fresh['source_uid']
            and marker['rig'] == rig.name_full and marker['scene'] == context.scene.name_full
            and ids and len(ids) == len(set(ids)) and list(names) == marker['names']
            and len(names) == len(set(names))
            and marker['registry_sha256'] == _text_hash(source.get(strands.REGISTRY_KEY))
            and marker['rig_record_sha256'] == _text_hash(source.get(hair.RECORD_KEY)))
        if not valid:
            _fail('The saved Hair preview owner changed; no native state was reclaimed.')
        fields = tuple(prop.identifier for prop in bpy.types.PoseBone.bl_rna.properties
                       if prop.identifier.startswith('wiggle_'))
        expected = marker['expected']
        if (set(expected) != set(names) or set(marker['original']) != set(names)
                or set(marker['groups']) != set(names)
                or any(set(expected[name]) != set(fields) or set(marker['original'][name]) != set(fields)
                       for name in names)):
            _fail('The saved Hair preview native field inventory changed.')
        bones = {name: rig.pose.bones[name] for name in names}
        if any(runtime.owns(rig, name) for name in names):
            _fail('A live physics owner already controls this saved Hair preview.')
        if any(_encode({field: runtime._raw(bone, field) for field in fields}) != expected[name]
               for name, bone in bones.items()):
            _fail('Saved Hair native settings changed outside the owned preview; those changes were preserved.')
        members = runtime._members(context.scene)
        paused = marker.get('paused', False)
        if type(paused) is not bool:
            _fail('Saved Hair physics pause ownership is unreadable.')
        owned_members = {(rig.name, name) for name in names}
        if ((paused and (owned_members & members or not all(bone.wiggle_mute for bone in bones.values())))
                or (not paused and not owned_members <= members)):
            _fail('Saved Hair native list ownership changed; no entries were removed.')
        original, groups = _decode(marker['original']), _decode(marker['groups'])
        scene_original, rig_original = _decode(marker['scene_original']), _decode(marker['rig_original'])
    except (KeyError, TypeError, IndexError, json.JSONDecodeError) as error:
        raise HairWiggleError('Saved Hair preview recovery metadata is incomplete.') from error
    return {'source': source, 'scene': context.scene, 'rig': rig, 'rig_name': rig.name,
        'names': names, 'bones': bones, 'fields': fields, 'backend': backend,
        'marker_raw': raw, 'original': original, 'groups': groups,
        'scene_original': scene_original, 'rig_original': rig_original,
        'before_raw': {name: {field: runtime._raw(bone, field) for field in fields} for name, bone in bones.items()},
        'before_groups': {name: runtime._group(bone) for name, bone in bones.items()},
        'before_scene': runtime._raw(context.scene, 'wiggle_enable'),
        'before_rig': runtime._raw(rig, 'wiggle_enable'),
        'before_history_pending': _HISTORY_PENDING, 'before_last_source_pointer': _LAST_SOURCE_POINTER,
        'layout': [(row.name, tuple(item.name for item in row.list)) for row in context.scene.wiggle.list],
        'needed': True, 'changed': False, 'stopped_confirmed': False, 'reason': ''}


def rollback_recovery(context, token):
    global _HISTORY_PENDING, _LAST_SOURCE_POINTER
    if not token.get('changed'):
        return
    source, rig, scene = token['source'], token['rig'], token['scene']
    if source.get(_RECOVERY_KEY) not in (None, token['marker_raw']) or context.scene != scene:
        _fail('Hair recovery rollback context changed; preserve the current scene.')
    if _backend() is not token['backend']:
        _fail('Hair recovery rollback backend changed.')
    with runtime._writes(token['backend']):
        for name, bone in token['bones'].items():
            if rig.pose.bones.get(name) != bone:
                _fail('A Hair bone was replaced during recovery rollback.')
            for field, saved in token['before_raw'][name].items():
                runtime._restore(bone, field, saved)
            runtime._restore_group(bone, token['before_groups'][name])
        runtime._restore(scene, 'wiggle_enable', token['before_scene'])
        runtime._restore(rig, 'wiggle_enable', token['before_rig'])
    runtime._restore_members(token, {'active': True,
        'paused': not any(name == rig.name and set(items) & set(token['names'])
                          for name, items in token['layout']), 'layout': token['layout']})
    source[_RECOVERY_KEY] = token['marker_raw']
    _HISTORY_PENDING = token['before_history_pending']
    _LAST_SOURCE_POINTER = token['before_last_source_pointer']
    token['changed'], token['stopped_confirmed'] = False, False


def recover_saved(context, source, token=None):
    """Explicitly clear only an authenticated Hair history residue, transactionally."""
    global _HISTORY_PENDING, _LAST_SOURCE_POINTER
    fresh = prepare_recovery(context, source)
    if token is not None and token.get('needed') and fresh.get('marker_raw') != token.get('marker_raw'):
        _fail('The saved Hair preview changed before recovery; verify it again.')
    token = fresh
    if not token['needed']:
        return token
    rig, scene = token['rig'], token['scene']
    token['changed'] = True
    try:
        with runtime._writes(token['backend']):
            for name, bone in token['bones'].items():
                for field, saved in token['original'][name].items():
                    runtime._restore(bone, field, saved)
                runtime._restore_group(bone, token['groups'][name])
            runtime._list_remove(scene, rig.name, token['names'])
            if not any(b.wiggle_head or b.wiggle_tail for b in rig.pose.bones):
                runtime._restore(rig, 'wiggle_enable', token['rig_original'])
            if not any(b.wiggle_head or b.wiggle_tail for ob in scene.objects
                       if ob.type == 'ARMATURE' and ob.pose is not None for b in ob.pose.bones):
                runtime._restore(scene, 'wiggle_enable', token['scene_original'])
        del source[_RECOVERY_KEY]
        token['stopped_confirmed'] = True
        _HISTORY_PENDING, _LAST_SOURCE_POINTER = False, source.as_pointer()
        return token
    except Exception:
        rollback_recovery(context, token)
        raise


def _callback_label(function):
    return str(getattr(function, '__module__', type(function).__module__)) + '.' + str(
        getattr(function, '__qualname__', getattr(function, '__name__', type(function).__name__)))


def _arp_callback_certificate(function):
    """Authenticate the reviewed live callback once, without importing ARP.

    Cache only immutable code and exact live bindings. A source change on disk
    cannot change this already-authenticated code; a reload/code/global/export
    replacement must obtain a fresh certificate and repeat the source proof.
    """
    global _ARP_INERT_CERTIFICATE
    from types import FunctionType, ModuleType
    name = getattr(function, '__module__', '')
    if (not isinstance(function, FunctionType) or not isinstance(name, str)
            or not (name == 'auto_rig_pro.src.rig_functions'
                    or name.endswith('.auto_rig_pro.src.rig_functions'))
            or function.__name__ != 'rig_layers_anim_update'):
        return False
    label = _callback_label(function)
    module = sys.modules.get(name)
    if (not isinstance(module, ModuleType) or vars(module).get('rig_layers_anim_update') is not function
            or function.__globals__ is not vars(module) or vars(module).get('bpy') is not bpy
            or function.__defaults__ is not None or function.__kwdefaults__ is not None
            or function.__closure__ is not None):
        _fail('The Auto Rig Pro callback has unverified live bindings: ' + label)
    filename = vars(module).get('__file__')
    cached = _ARP_INERT_CERTIFICATE
    if (cached is not None and cached['function'] is function and cached['code'] is function.__code__
            and cached['module'] is module and cached['filename'] == filename):
        return True
    from pathlib import Path
    try:
        path = Path(filename).resolve()
        if Path(function.__code__.co_filename).resolve() != path:
            _fail('The Auto Rig Pro callback source path changed: ' + label)
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != _ARP_INERT_SOURCE_SHA256:
            _fail('The Auto Rig Pro callback source version is not reviewed: ' + label)
        compiled = compile(payload, function.__code__.co_filename, 'exec', dont_inherit=True)
        definitions = [code for code in compiled.co_consts if isinstance(code, type(function.__code__))
                       and code.co_name == 'rig_layers_anim_update']
        if (len(definitions) != 1 or any(getattr(function.__code__, field) != getattr(definitions[0], field)
                                        for field in _ARP_INERT_CODE_FIELDS)):
            _fail('The Auto Rig Pro callback code differs from its reviewed source: ' + label)
    except (OSError, TypeError, ValueError, SyntaxError) as error:
        raise HairWiggleError('Cannot authenticate the Auto Rig Pro callback ' + label + ': ' + str(error)) from error
    _ARP_INERT_CERTIFICATE = dict(function=function, code=function.__code__, module=module, filename=filename)
    return True


def _arp_action_has_flag_path(action):
    # Read paths only, including every layered channelbag. Never read or copy
    # keyframe arrays, create a legacy channelbag, or inspect unrelated Actions.
    try:
        legacy = action.fcurves
    except (AttributeError, RuntimeError):
        legacy = ()
    if any(curve.data_path in _ARP_INERT_PATHS for curve in legacy):
        return True
    for layer in getattr(action, 'layers', ()):
        for strip in getattr(layer, 'strips', ()):
            for bag in getattr(strip, 'channelbags', ()):
                if any(curve.data_path in _ARP_INERT_PATHS for curve in bag.fcurves):
                    return True
    return False


def _arp_scene_flag_animated(scene):
    data = scene.animation_data
    if data is None:
        return False
    if any(curve.data_path in _ARP_INERT_PATHS for curve in data.drivers):
        return True
    actions = [data.action] if data.action is not None else []
    pending = [strip for track in data.nla_tracks for strip in track.strips]
    seen_strips = set()
    while pending:
        strip = pending.pop()
        pointer = strip.as_pointer()
        if pointer in seen_strips:
            continue
        seen_strips.add(pointer)
        action = getattr(strip, 'action', None)
        if action is not None:
            actions.append(action)
        pending.extend(getattr(strip, 'strips', ()))
    seen_actions = set()
    for action in actions:
        pointer = action.as_pointer()
        if pointer not in seen_actions:
            seen_actions.add(pointer)
            if _arp_action_has_flag_path(action):
                return True
    return False


def _arp_callback_is_inert(function, scene):
    if not _arp_callback_certificate(function):
        return False
    label = _callback_label(function)
    prop = bpy.types.Scene.bl_rna.properties.get('arp_layers_animated')
    if (scene is not bpy.context.scene or prop is None or prop.type != 'BOOLEAN'
            or getattr(prop, 'is_array', False)):
        _fail('Auto Rig Pro Animated Layers has an unverified Scene setting: ' + label)
    value = getattr(scene, 'arp_layers_animated', None)
    if type(value) is not bool or value is not False:
        _fail('Turn off Auto Rig Pro Animated Layers before Hair preview: ' + label)
    if _arp_scene_flag_animated(scene):
        _fail('Auto Rig Pro Animated Layers must be unanimated during Hair preview: ' + label)
    return True


def _handler_gate(backend, armature, names):
    old = _old()
    known = {backend.wiggle_pre, backend.wiggle_post, _frame_guard, old._frame_guard}
    for module_name, function_name in (('bone_collections', '_frame_visibility'),
                                      ('forearm_twist', '_frame_post'),
                                      ('playback_normal_bridge', '_guard')):
        module = sys.modules.get(__package__ + '.' + module_name)
        function = getattr(module, function_name, None)
        if function is None:
            continue
        if (module_name == 'forearm_twist' and function in bpy.app.handlers.frame_change_post
                and (getattr(module, '_INITIALIZE_PENDING', True)
                     or getattr(module, '_SCENE_CLEANUP_PENDING', True))):
            _fail('Finish pending Forearm preview recovery before starting Hair preview.')
        known.add(function)
    # This exact adapter only changes its coordinator-owned PHYS bones. Its
    # ownership is checked below and on every guarded frame, not inferred from
    # the callback's name or module string.
    skirt = sys.modules.get(__package__ + '.skirt_wiggle')
    peer_guard = getattr(skirt, '_frame_pre', None)
    if peer_guard is not None:
        for saved in getattr(skirt, '_SESSIONS', {}).values():
            lease = saved.get('lease')
            if lease is None:
                _fail('A Dress preview has no verified shared Wiggle ownership lease.')
            runtime.validate(lease)
            if saved['rig'] == armature and set(saved['names']).intersection(names):
                _fail('Hair and Dress physics must own separate bones.')
        known.add(peer_guard)
    for collection in (bpy.app.handlers.frame_change_pre, bpy.app.handlers.frame_change_post):
        for function in collection:
            if function in known:
                continue
            proof = old._FRAME_OWNERSHIP.get(function)
            if proof and (proof[0] != armature or not proof[1].intersection(names)):
                continue
            # This exact reviewed ARP callback returns before its display writes
            # only while this predicate remains true. Never grant a permanent
            # known/proof entry for it; repeat the predicate on every guard.
            module_name = getattr(function, '__module__', '')
            if (getattr(function, '__name__', '') == 'rig_layers_anim_update'
                    and isinstance(module_name, str)
                    and (module_name == 'auto_rig_pro.src.rig_functions'
                         or module_name.endswith('.auto_rig_pro.src.rig_functions'))
                    and _arp_callback_is_inert(function, bpy.context.scene)):
                continue
            _fail('An unverified frame callback may modify this Hair pose or Actions; '
                  'provide reviewed disjoint pose-write ownership first. Callback: '
                  + str(module_name) + '.' + str(getattr(function, '__qualname__',
                    getattr(function, '__name__', type(function).__name__))))


def _preflight(context, source, registry, profiles, strand_ids, backend):
    if _SESSION is not None:
        _fail('Stop the existing Hair preview before starting another stage.')
    if context.scene != bpy.context.scene or context.mode not in {'OBJECT', 'POSE'}:
        _fail('Start preview in the active Scene in Object or Pose Mode.')
    fresh = strands.read(source, validate=True)
    if registry != fresh:
        _fail('The supplied strand registry is stale; refresh its strict ownership proof.')
    armature = source.get(hair.RIG_KEY)
    from . import limb_ik
    if (not isinstance(armature, bpy.types.Object) or armature.type != 'ARMATURE'
            or context.scene.objects.get(armature.name) != armature
            or armature.data.pose_position != 'POSE' or not source.is_editable
            or not armature.is_editable or source.library or source.override_library
            or armature.library or armature.override_library or armature.data.library
            or not limb_ik._single_armature_user(armature)):
        _fail('Preview needs a local single-user Armature in Pose position in this Scene.')
    ids = tuple(strand_ids)
    by_id = {item['strand_id']: item for item in fresh['strands']}
    if not ids or len(set(ids)) != len(ids) or set(ids) - set(by_id):
        _fail('Choose one or more unique registry-owned Hair strands.')
    if not isinstance(profiles, dict) or any(key not in profiles for key in ids):
        _fail('Pass a strand_id to effective settings mapping.')
    effective = {key: _old()._profile(profiles[key]) for key in ids}
    all_names = {name for item in fresh['strands'] for name in item['bones']}
    names = tuple(name for key in ids for name in by_id[key]['bones'])
    if len(names) != len(set(names)):
        _fail('Selected Hair chains must own independent bones.')
    _old()._hair_dependencies(armature, [armature.pose.bones[name] for name in all_names])
    _handler_gate(backend, armature, all_names)
    scene = context.scene
    if scene.render.fps_base != 1.0 or scene.render.fps < 1:
        _fail('Wiggle 2 requires fps_base=1 for the supported time mapping.')
    if any(path.startswith('wiggle') for path in _old()._driver_paths(scene) + _old()._animated_paths(scene)):
        _fail('Animated Scene Wiggle settings prevent a safe temporary preview.')
    if any(getattr(scene.wiggle, key) for key in ('is_rendering', 'reset', 'is_preroll')):
        _fail('Finish the current Wiggle render/reset/preroll operation first.')
    unit_scale, gravity_length = scene.unit_settings.scale_length, scene.gravity.length
    if (not math.isfinite(unit_scale) or unit_scale <= 0
            or not math.isfinite(gravity_length)):
        _fail('Scene length units and gravity must be finite and usable.')
    if gravity_length <= 1e-8 and any(base['gravity'] or any(knot['gravity'] for knot in knots)
                                    for base, knots in effective.values()):
        _fail('Nonzero Hair gravity needs a nonzero Scene gravity vector; the author vector is preserved.')
    determinant = armature.matrix_world.determinant()
    if not math.isfinite(determinant) or abs(determinant) < 1e-10:
        _fail('A zero Armature world scale prevents native world-space simulation.')
    peer = runtime.owned_bones(scene)
    for name in names:
        parent = armature.pose.bones[name].parent
        while parent is not None:
            if (armature, parent.name) in peer:
                _fail('A Hair chain cannot feed back into another component\'s simulated ancestor.')
            parent = parent.parent
    for ob in scene.objects:
        if ob.type == 'ARMATURE' and any(path.startswith('wiggle') or '.wiggle' in path
                for path in _old()._driver_paths(ob) + _old()._animated_paths(ob)):
            _fail('Another Armature has animated Wiggle settings.')
    return armature, fresh, ids, by_id, effective, names


def _settings(scene, armature, ids, by_id, effective):
    settings = {}
    acceleration = scene.gravity.length * scene.unit_settings.scale_length
    for key in ids:
        chain = by_id[key]['bones']
        lengths = [(armature.data.bones[name].tail_local - armature.data.bones[name].head_local).length
                   for name in chain]
        total = sum(lengths)
        if not math.isfinite(total) or total <= 1e-8:
            _fail('A selected Hair chain has invalid Rest length.')
        distance = 0.0
        for name, length in zip(chain, lengths):
            distance += length
            values = _old()._depth_values(effective[key], distance / total)
            settings[name] = {'wiggle_stiff': 800.0 * values['recovery'] ** 2,
                'wiggle_damp': 20.0 * values['damping'],
                'wiggle_gravity': values['gravity'] / acceleration if acceleration else 0.0,
                'wiggle_stretch': values['stretch'], 'wiggle_mass': values['mass'],
                'wiggle_chain': True, 'wiggle_head_mute': False, 'wiggle_tail_mute': False,
                'wiggle_wind_ob': None, 'wiggle_collider': None, 'wiggle_collider_collection': None,
                'wiggle_wind_ob_head': None, 'wiggle_collider_head': None,
                'wiggle_collider_collection_head': None}
    return settings


def _snapshot(context, source, armature, registry, ids, names, backend):
    old = _old()
    all_names = tuple(name for item in registry['strands'] for name in item['bones'])
    return {'scene': context.scene, 'source': source, 'armature': armature,
        'source_name': source.name, 'source_pointer': source.as_pointer(),
        'registry': registry, 'ids': ids, 'names': names, 'backend': backend,
        'channels': [old._channels(armature.pose.bones[name]) for name in names],
        # Preview never writes Action/keyframe contents. Keep complete existing
        # AnimData/NLA/Driver/binding proof; full Action contents remain an
        # explicit native acceptance operation, not an interactive Start scan.
        'actions': _animation_proof(include_actions=False), 'started_playback': False,
        'registry_raw': source.get(strands.REGISTRY_KEY), 'rig_record': source.get(hair.RECORD_KEY),
        'rig_pointer': source.get(hair.RIG_KEY), 'data_pointer': source.data,
        'source_counts': (len(source.data.vertices), len(source.data.edges), len(source.data.polygons)),
        'bone_count': len(armature.data.bones), 'unit_scale': context.scene.unit_settings.scale_length,
        'gravity': tuple(context.scene.gravity), 'lastframe': context.scene.frame_current,
        'rest': [(armature.data.bones[name], old._bone_proof(armature.data.bones[name])) for name in all_names],
        'anchors': [(armature.data.bones[item['anchor']['name']],
                     old._bone_proof(armature.data.bones[item['anchor']['name']]))
                    for item in registry['strands'] if item['anchor']],
        'physics_paused': False, 'lease': None}


def _playback_bounds(scene):
    preview = bool(scene.use_preview_range)
    return (preview, int(scene.frame_preview_start if preview else scene.frame_start),
            int(scene.frame_preview_end if preview else scene.frame_end), int(scene.frame_step))


def _native_playback_wrap(session, scene, frame):
    """Admit only the exact boundary of the same live native playback range.

    A manual seek that exactly imitates this boundary during playback is not
    distinguishable through frame handlers; it receives the same safe reseed.
    Other backwards seeks, changed ranges and dropped boundary frames refuse.
    """
    try:
        bounds = _playback_bounds(scene)
        _preview, first, last, step = bounds
        window, screen = session.get('playback_window'), session.get('playback_screen')
        manager = bpy.context.window_manager
        return bool(scene == session['scene'] and bounds == session.get('playback_bounds')
            and step == 1 and first < last and frame == first and session['lastframe'] == last
            and window is not None and screen is not None and manager is not None
            and any(item == window for item in manager.windows)
            and window.scene == scene and window.screen == screen and screen.is_animation_playing)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False


def _start_playback(context, session):
    """Only an explicit Start/Resume command may start an idle GUI timeline."""
    if context.screen and context.window and not context.screen.is_animation_playing:
        with context.temp_override(window=context.window, screen=context.screen):
            result = bpy.ops.screen.animation_play()
        session['started_playback'] = bool(context.screen.is_animation_playing)
        if 'FINISHED' not in result or not session['started_playback']:
            _fail('Blender did not start timeline playback.')
    if context.screen and context.window and context.screen.is_animation_playing:
        session['playback_window'], session['playback_screen'] = context.window, context.screen
        session['playback_bounds'] = _playback_bounds(context.scene)


def start_preview(context, source, registry, profiles, strand_ids):
    global _SESSION, _LAST_REASON, _LAST_SOURCE_POINTER, _HISTORY_PENDING
    from .animation_physics import require_enabled
    try:
        _LAST_SOURCE_POINTER = source.as_pointer()
        require_enabled(source, context=context, component='HAIR')
        if source.get(_RECOVERY_KEY) is not None and _SESSION is None:
            recover_saved(context, source)
        backend = _backend()
        armature, fresh, ids, by_id, effective, names = _preflight(
            context, source, registry, profiles, strand_ids, backend)
        settings = _settings(context.scene, armature, ids, by_id, effective)
        session = _snapshot(context, source, armature, fresh, ids, names, backend)
        session['lease'] = runtime.acquire(context, 'HAIR', source, armature, names, backend)
    except (ValueError, RuntimeError, AttributeError, ReferenceError) as error:
        _LAST_REASON = str(error)
        raise HairWiggleError(str(error)) from error
    _SESSION = session
    _LAST_SOURCE_POINTER = session['source_pointer']
    try:
        register_guards()
        runtime.configure(session['lease'], settings)
        _write_marker(session)
        from .physics_dependencies import note_verified
        note_verified('HAIR', backend)
        if not bpy.app.timers.is_registered(_mode_timer):
            bpy.app.timers.register(_mode_timer, first_interval=0.1)
        _start_playback(context, session)
        _LAST_REASON = ''
        _HISTORY_PENDING = False
        return status(context)
    except Exception as error:
        try:
            stop_preview(context, reason='Start failed; restored only the owned Hair preview.')
        except Exception as rollback:
            raise HairWiggleError('Start failed and owned Hair cleanup needs attention: ' + str(rollback)) from error
        raise HairWiggleError('Wiggle 2 Hair preview start failed: ' + str(error)) from error


def _changed(session):
    source, armature, old = session['source'], session['armature'], _old()
    if (bpy.context.scene != session['scene'] or bpy.context.mode not in {'OBJECT', 'POSE'}
            or armature.mode == 'EDIT' or source.mode == 'EDIT'):
        return 'Scene or mode changed.'
    if (source.get(strands.REGISTRY_KEY) != session['registry_raw']
            or source.get(hair.RECORD_KEY) != session['rig_record']
            or source.get(hair.RIG_KEY) != session['rig_pointer'] or source.data != session['data_pointer']
            or (len(source.data.vertices), len(source.data.edges), len(source.data.polygons)) != session['source_counts']):
        return 'Hair binding or registry changed.'
    if source.get(_RECOVERY_KEY) != session.get('marker_raw'):
        return 'Hair preview recovery ownership changed.'
    if (len(armature.data.bones) != session['bone_count']
            or any(old._bone_proof(bone) != saved for bone, saved in session['rest'] + session['anchors'])):
        return 'Hair Rest or parent relationships changed.'
    if (session['scene'].unit_settings.scale_length != session['unit_scale']
            or tuple(session['scene'].gravity) != session['gravity']):
        return 'Scene gravity or length units changed.'
    old._hair_dependencies(armature, [armature.pose.bones[bone.name] for bone, _ in session['rest']])
    _handler_gate(session['backend'], armature, {bone.name for bone, _ in session['rest']})
    runtime.validate(session['lease'])
    return ''


def set_preview_paused(context, source, paused):
    session = _SESSION
    if (type(paused) is not bool or session is None or _STOPPING
            or session['source'] != source or session['scene'] != context.scene):
        _fail('The Hair preview changed. Use its current Physics controls again.')
    reason = _changed(session)
    if reason:
        _fail(reason)
    if not _animation_same(session['actions']):
        _fail('Animation associations changed; stop this owned Hair preview before switching Physics.')
    was_paused = bool(session.get('physics_paused', False))
    if was_paused and not paused and session.get('channels'):
        context.view_layer.update()
    pose_at_switch = [_old()._channels(saved[0]) for saved in session.get('channels', ())]
    channels_after = list(session.get('channels', ()))
    held = session.get('paused_channels')
    if was_paused and not paused and held is not None:
        # A paused owner is detached. New author/keyed pose values become its
        # return pose before a fresh seed; do not restore the stale Start pose
        # over those edits on a subsequent Stop.
        prior = {saved[0].name: saved for saved in held}
        channels_after = [current if current != prior.get(current[0].name) else original
                          for original, current in zip(channels_after, pose_at_switch)]
    before = runtime._live(session['lease'])
    marker_before = source.get(_RECOVERY_KEY)
    try:
        runtime.set_paused(session['lease'], paused)
        _write_marker(session)
    except Exception:
        runtime._rollback(session['lease'], before)
        if marker_before is None:
            source.pop(_RECOVERY_KEY, None)
        else:
            source[_RECOVERY_KEY] = marker_before
        session['marker_raw'] = marker_before
        raise
    session['physics_paused'] = paused
    if paused and not was_paused:
        session['paused_channels'] = pose_at_switch
    elif not paused:
        session['paused_channels'] = None
        if 'channels' in session:
            session['channels'] = channels_after
    return status(context)


def resume_preview(context, source):
    global _LAST_REASON, _LAST_SOURCE_POINTER
    from .animation_physics import require_enabled
    require_enabled(source, context=context, component='HAIR')
    session = _SESSION
    if (session is None or _STOPPING or session['source'] != source
            or session['scene'] != context.scene):
        _fail('The Hair preview changed. Use its current Physics controls again.')
    before = runtime._live(session['lease'])
    marker_before = source.get(_RECOVERY_KEY)
    marker_raw_before = session.get('marker_raw')
    paused_before, playback_before = session['physics_paused'], session['started_playback']
    channels_before, held_before = session.get('channels'), session.get('paused_channels')
    set_preview_paused(context, source, False)
    try:
        _start_playback(context, session)
    except Exception:
        runtime._rollback(session['lease'], before)
        if marker_before is None:
            source.pop(_RECOVERY_KEY, None)
        else:
            source[_RECOVERY_KEY] = marker_before
        session['marker_raw'] = marker_raw_before
        session['physics_paused'], session['started_playback'] = paused_before, playback_before
        if channels_before is not None:
            session['channels'] = channels_before
        session['paused_channels'] = held_before
        raise
    _LAST_REASON, _LAST_SOURCE_POINTER = '', session['source_pointer']
    return status(context)


def stop_preview(context=None, *, reason='Stopped', source=None):
    global _SESSION, _LAST_REASON, _STOPPING, _LAST_SOURCE_POINTER
    session = _SESSION
    if session is not None and source is not None and session['source'] != source:
        _fail('The current Hair preview belongs to another source; use its own Stop control.')
    if session is None or _STOPPING:
        if session is None and source is not None and context is not None:
            recover_saved(context, source)
        return status(context)
    _LAST_SOURCE_POINTER = session.get('source_pointer', _LAST_SOURCE_POINTER)
    _STOPPING = True
    errors = []
    preserved_pose = []
    try:
        # Never restore any peer pose, timeline, Auto Key or Shape Key values.
        # A failed native release keeps the lease/session visible for retry.
        if not session.get('lease_released', False):
            try:
                runtime.release(session['lease'])
                session['lease_released'] = True
            except Exception as error:
                _LAST_REASON = reason + ' Native Hair cleanup needs attention: ' + str(error)
                raise HairWiggleError(_LAST_REASON) from error
        for saved in session['channels']:
            try:
                # Cached RNA refs cannot overwrite a replacement same-name bone.
                if session['armature'].pose.bones.get(saved[0].name) != saved[0]:
                    continue
                if session.get('physics_paused', False) and session.get('paused_channels') is not None:
                    held = next((item for item in session['paused_channels'] if item[0] == saved[0]), None)
                    if held is None or _old()._channels(saved[0]) != held:
                        preserved_pose.append(saved[0].name)
                        continue
                _old()._restore_channels(saved)
            except (ReferenceError, RuntimeError, TypeError, ValueError) as error:
                errors.append(str(error))
        _LAST_REASON = reason
        if preserved_pose:
            _LAST_REASON += ' Pose edits made while Physics was off were preserved.'
        if errors:
            _LAST_REASON += ' ' + '; '.join(errors)
            raise HairWiggleError(_LAST_REASON)
        source = session['source']
        if source.get(_RECOVERY_KEY) == session.get('marker_raw'):
            source.pop(_RECOVERY_KEY, None)
        elif source.get(_RECOVERY_KEY) is not None:
            _LAST_REASON += ' External recovery metadata was preserved.'
        _SESSION = None
        if not _TIMER_RUNNING and bpy.app.timers.is_registered(_mode_timer):
            bpy.app.timers.unregister(_mode_timer)
        if not _animation_same(session['actions']):
            _LAST_REASON += ' Animation associations changed externally during preview; those edits were preserved.'
            raise HairWiggleError(_LAST_REASON)
        return status(context)
    finally:
        _STOPPING = False


@persistent
def _frame_guard(scene, *args):
    session = _SESSION
    if session is None or _STOPPING:
        return
    if session.get('physics_paused', False):
        # Detached owners cannot solve. Resume repeats the full author/Rest
        # proof; Stop checks exact native ownership before releasing its flags.
        if scene == session['scene']:
            session['lastframe'] = scene.frame_current
        return
    try:
        reason = _changed(session)
        frame = scene.frame_current
        if scene != session['scene']:
            reason = 'Scene changed.'
        elif not reason and frame < session['lastframe']:
            if _native_playback_wrap(session, scene, frame):
                # Seed only these owned endpoints in the currently evaluated
                # pose, then skip the solver's discontinuity frame. The peer
                # adapter reseeds its own bones independently on its guard.
                bpy.context.view_layer.update()
                runtime.reseed(session['lease'], skip_frame=True)
                session['native_wraps'] = session.get('native_wraps', 0) + 1
            else:
                reason = 'Timeline wrapped, moved backwards or skipped more than four frames.'
        elif not reason and frame - session['lastframe'] > 4:
            reason = 'Timeline wrapped, moved backwards or skipped more than four frames.'
        if reason:
            stop_preview(reason=reason)
        else:
            session['lastframe'] = frame
    except (ValueError, RuntimeError, AttributeError, ReferenceError) as error:
        try:
            stop_preview(reason='Hair preview guard stopped: ' + str(error))
        except (ValueError, RuntimeError, AttributeError, ReferenceError):
            pass  # Keep failed cleanup visible; never clear another lease.


@persistent
def _lifecycle_guard(*args):
    if _SESSION is not None:
        stop_preview(reason='Save, undo, redo, load or render ended Hair preview.')


@persistent
def _history_post(*args):
    """No RNA reads or reconnects after native Undo/Redo/load replacement."""
    global _SESSION, _HISTORY_PENDING, _LAST_REASON
    _SESSION = None
    runtime.forget('HAIR')
    _HISTORY_PENDING = True
    _LAST_REASON = 'History/load ended the transient Hair lease; use explicit Physics controls to verify it again.'
    if bpy.app.timers.is_registered(_mode_timer):
        bpy.app.timers.unregister(_mode_timer)


def _mode_timer():
    global _TIMER_RUNNING
    if not _GUARDS_REGISTERED or _SESSION is None:
        return None
    if _SESSION.get('physics_paused', False):
        return 0.1
    _TIMER_RUNNING = True
    try:
        try:
            reason = _changed(_SESSION)
            if reason:
                stop_preview(reason=reason)
        except (ValueError, RuntimeError, AttributeError, ReferenceError) as error:
            try:
                stop_preview(reason='Hair preview guard stopped: ' + str(error))
            except (ValueError, RuntimeError, AttributeError, ReferenceError):
                pass
    finally:
        _TIMER_RUNNING = False
    return 0.1 if _SESSION is not None else None


def register_guards():
    global _GUARDS_REGISTERED
    if _frame_guard not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.insert(0, _frame_guard)
    for name in _LIFECYCLE_LISTS:
        collection = getattr(bpy.app.handlers, name)
        if _lifecycle_guard not in collection:
            collection.insert(0, _lifecycle_guard)
    for name in ('undo_post', 'redo_post', 'load_post'):
        collection = getattr(bpy.app.handlers, name)
        if _history_post not in collection:
            collection.append(_history_post)
    _GUARDS_REGISTERED = True


def unregister_guards():
    global _GUARDS_REGISTERED
    stop_preview(reason='Character Designer unregistered.')
    _GUARDS_REGISTERED = False
    if bpy.app.timers.is_registered(_mode_timer):
        bpy.app.timers.unregister(_mode_timer)
    for name, function in [('frame_change_pre', _frame_guard)] + [
            (name, _lifecycle_guard) for name in _LIFECYCLE_LISTS] + [
            (name, _history_post) for name in ('undo_post', 'redo_post', 'load_post')]:
        collection = getattr(bpy.app.handlers, name)
        while function in collection:
            collection.remove(function)
