"""Fresh, bounded full-content proofs for one wrist transaction.

This typed field-major policy is deliberately separate from persisted worklist
fingerprints. No result or native value is cached between calls. Every field
covered by the existing motion fingerprint remains covered; only homogeneous
native point collections use bulk RNA reads. Unknown values fail closed.
"""
from array import array
import ast
import hashlib
import json
import math
import re
import struct
import sys

from . import animation_worklist_fingerprint as _fp


POLICY = 'character-designer.wrist-content/1'
MAX_BUFFER_BYTES = 8 * 1024 * 1024
_PATH = re.compile(r'^pose\.bones\[("(?:[^"\\]|\\.)*")\]\.(location|rotation_euler|rotation_quaternion|rotation_axis_angle|scale)$')
_ACTION_EXCLUDE = _fp._ID_METADATA | {'slots', 'layers', 'fcurves', 'groups',
    'pose_markers', 'is_action_layered', 'is_action_legacy', 'is_empty', 'curve_frame_range'}
_CURVE_EXCLUDE = _fp._CURVE_UI | {'group', 'driver', 'is_driver',
    'keyframe_points', 'sampled_points', 'modifiers'}
_GROUP_EXCLUDE = {'name', 'channels', 'select', 'lock', 'show_expanded',
    'show_expanded_graph', 'use_pin', 'color_set', 'colors', 'is_custom_color_set'}


def _require(value, message):
    if not value:
        raise _fp.FingerprintUnknown(message)


def _bounded(values, limit, label):
    return _fp._bounded(values, limit, label)


def _flatten(value):
    for item in value:
        if type(item) in (bool, int, float):
            yield item
        else:
            yield from _flatten(item)


def _finite_numbers(values):
    """Validate every number and normalize signed zero in bounded native arrays."""
    try:
        import numpy
    except ImportError:
        for index, value in enumerate(values):
            _require(math.isfinite(value), 'Fingerprint payload contains a non-finite number.')
            if value == 0.0:
                values[index] = 0.0
    else:
        view = numpy.frombuffer(values, dtype=numpy.float64)
        _require(bool(numpy.isfinite(view).all()), 'Fingerprint payload contains a non-finite number.')
        # Adding positive zero changes only the sign bit of negative zero;
        # all finite nonzero float64 values remain bit-exact.
        numpy.add(view, 0.0, out=view)


def _enum_codes(values, allowed, name):
    try:
        import numpy
    except ImportError:
        valid = all(value in allowed for value in values)
    else:
        view = numpy.frombuffer(values, dtype=numpy.int32)
        valid = bool(numpy.isin(view, tuple(allowed)).all())
    _require(valid, 'Unknown native point enum code: ' + name)


class _Writer:
    def __init__(self, *, force_scalar=False):
        self.hash = hashlib.sha256()
        self.force_scalar = force_scalar
        self.schema_cache = {}
        self.point_plans = {}
        self.no_bulk = set()
        self.points = 0
        self.curves = 0
        self.stats = dict(bulk_fields=0, scalar_fields=0, numeric_values=0,
                          enum_values=0, max_buffer_bytes=0)
        self.put((POLICY,))

    def put(self, value):
        raw = _fp.canonical_bytes(value)
        self.hash.update(struct.pack('!Q', len(raw)))
        self.hash.update(raw)

    def settings(self, owner, *, exclude=()):
        self.put(_fp._rna_settings(owner, exclude=exclude, schema_cache=self.schema_cache))

    def _plan(self, first, exclude):
        descriptor = getattr(first, 'bl_rna', None)
        properties = getattr(descriptor, 'properties', None)
        _require(properties is not None, 'Native points have no inspectable RNA schema.')
        identity = getattr(descriptor, 'as_pointer', lambda: 0)()
        key = (identity, frozenset(exclude)) if type(identity) is int and identity > 0 else None
        if key is not None and key in self.point_plans:
            return self.point_plans[key][1]
        plan = []
        for prop in _bounded(properties, _fp.MAX_ITEMS, 'point RNA properties'):
            if prop.identifier in set(exclude) | {'rna_type'}:
                continue
            kind = prop.type
            is_array = kind in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'} and bool(getattr(prop, 'is_array', False))
            dimensions = tuple(getattr(prop, 'array_dimensions', ())) if is_array else ()
            width = getattr(prop, 'array_length', 0) if is_array else 1
            plan.append((prop, prop.identifier, kind, is_array, dimensions, width))
        plan = tuple(sorted(plan, key=lambda row: row[1]))
        if key is not None:
            self.point_plans[key] = (descriptor, plan)
        return plan

    @staticmethod
    def _native_points(first):
        # Foreach is only trusted for Blender's homogeneous native point types.
        # Ordinary test doubles take the complete scalar path.
        try:
            import bpy
            return isinstance(first, (bpy.types.Keyframe, bpy.types.FCurveSample))
        except (ImportError, AttributeError, TypeError):
            return False

    def _emit_array(self, values):
        if sys.byteorder == 'little' and values.itemsize > 1:
            values.byteswap()
        raw = memoryview(values).cast('B')
        self.hash.update(struct.pack('!Q', len(raw)))
        self.hash.update(raw)

    def _numeric(self, collection, prop, name, kind, is_array, width, native):
        if kind not in {'FLOAT', 'INT', 'BOOLEAN'} or type(width) is not int or width < 1:
            return False
        code = {'FLOAT': 'd', 'INT': 'i', 'BOOLEAN': 'b'}[kind]
        packed = {'FLOAT': '!d', 'INT': '!i', 'BOOLEAN': '!b'}[kind]
        _require(array(code).itemsize == struct.calcsize(packed), 'Unsupported native numeric array width.')
        count = len(collection)
        self.put(('NUMERIC', kind, count, width))
        key = (getattr(collection[0].bl_rna, 'identifier', ''), name)
        size = count * width * array(code).itemsize
        if native and not self.force_scalar and key not in self.no_bulk and size <= MAX_BUFFER_BYTES:
            values = array(code, [0]) * (count * width)
            try:
                collection.foreach_get(name, values)
                checks = (0, count-1) if count > 1 else (0,)
                matched = all(tuple(values[i*width:(i+1)*width]) == (
                    tuple(_flatten(getattr(collection[i], name))) if is_array else (getattr(collection[i], name),))
                    for i in checks)
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError, OverflowError):
                matched = False
            if matched:
                if kind == 'BOOLEAN':
                    _require(all(value in (0, 1) for value in values), 'Unknown native Boolean point value: ' + name)
                if kind == 'FLOAT':
                    _finite_numbers(values)
                self.stats['bulk_fields'] += 1
                self.stats['numeric_values'] += len(values)
                self.stats['max_buffer_bytes'] = max(self.stats['max_buffer_bytes'], size)
                self._emit_array(values)
                return True
            self.no_bulk.add(key)
        # The length prefix is identical to successful foreach_get encoding.
        self.hash.update(struct.pack('!Q', count * width * struct.calcsize(packed)))
        for owner in collection:
            value = getattr(owner, name)
            numbers = tuple(_flatten(value)) if is_array else (value,)
            _require(len(numbers) == width, 'Point RNA numeric dimensions changed: ' + name)
            for number in numbers:
                _require(type(number) is {'FLOAT': float, 'INT': int, 'BOOLEAN': bool}[kind],
                         'Point RNA numeric type changed: ' + name)
                _require(kind != 'FLOAT' or math.isfinite(number), 'Fingerprint payload contains a non-finite number.')
                self.hash.update(struct.pack(packed, 0.0 if kind == 'FLOAT' and number == 0.0 else number))
        self.stats['scalar_fields'] += 1
        return True

    def _enum(self, collection, prop, name, is_array, native):
        if is_array or getattr(prop, 'is_enum_flag', False):
            return False
        table = tuple(sorted((item.identifier, item.value) for item in prop.enum_items if item.identifier))
        # Dynamic/ambiguous tables retain exact identifiers through scalar RNA.
        if (not table or len({n for n, _ in table}) != len(table)
                or len({v for _, v in table}) != len(table)
                or any(type(v) is not int or not -(2**31) <= v < 2**31 for _, v in table)):
            return False
        codes = dict(table)
        allowed = frozenset(codes.values())
        count = len(collection)
        self.put(('ENUM', count, table))
        key = (getattr(collection[0].bl_rna, 'identifier', ''), name)
        size = count * array('i').itemsize
        if native and not self.force_scalar and key not in self.no_bulk and size <= MAX_BUFFER_BYTES:
            values = array('i', [0]) * count
            try:
                collection.foreach_get(name, values)
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError, OverflowError):
                matched = False
            else:
                _enum_codes(values, allowed, name)
                checks = (0, count-1) if count > 1 else (0,)
                matched = all(codes.get(getattr(collection[i], name)) == values[i] for i in checks)
            if matched:
                self.stats['bulk_fields'] += 1
                self.stats['enum_values'] += count
                self.stats['max_buffer_bytes'] = max(self.stats['max_buffer_bytes'], size)
                self._emit_array(values)
                return True
            self.no_bulk.add(key)
        self.hash.update(struct.pack('!Q', count * 4))
        for owner in collection:
            identifier = getattr(owner, name)
            _require(identifier in codes, 'Unknown native point enum identifier: ' + name)
            self.hash.update(struct.pack('!i', codes[identifier]))
        self.stats['scalar_fields'] += 1
        return True

    def points_sequence(self, collection, *, exclude):
        count = len(collection)
        _require(count <= _fp.MAX_CURVE_POINTS, 'Fingerprint collection exceeds its bound: curve points')
        self.points += count
        _require(self.points <= _fp.MAX_ACTION_POINTS, 'Action exceeds its total key/sample point bound.')
        self.put(('POINTS', count))
        if not count:
            return
        first = collection[0]
        native = self._native_points(first)
        plan = self._plan(first, exclude)
        if not native:
            shape = tuple(row[1:] for row in plan)
            _require(all(tuple(row[1:] for row in self._plan(owner, exclude)) == shape for owner in collection),
                     'Point RNA schema changed during content read.')
        self.put(('POINT_SCHEMA', tuple((n, k, a, d, w) for _, n, k, a, d, w in plan)))
        for prop, name, kind, is_array, dimensions, width in plan:
            self.put(('FIELD', name))
            if self._numeric(collection, prop, name, kind, is_array, width, native):
                continue
            if kind == 'ENUM' and self._enum(collection, prop, name, is_array, native):
                continue
            # All future pointer/collection/unsupported properties get the same
            # strict reader as the original full fingerprint, never omission.
            for owner in collection:
                one = _fp._rna_settings(owner, exclude=exclude, schema_cache=self.schema_cache)
                _require(name in one, 'Point RNA schema changed during content read.')
                self.put(one[name])

    def curve(self, curve):
        self.curves += 1
        _require(self.curves <= _fp.MAX_CURVES, 'Fingerprint collection exceeds its bound: curves')
        _require(getattr(curve, 'driver', None) is None and not getattr(curve, 'is_driver', False),
                 'FCurve drivers are outside the plain FK content proof.')
        self.put(('CURVE',))
        self.settings(curve, exclude=_CURVE_EXCLUDE)
        for name, excluded in (('keyframe_points', _fp._KEY_UI), ('sampled_points', {'select'})):
            self.put(name)
            self.points_sequence(getattr(curve, name), exclude=excluded)
        modifiers = tuple(_bounded(curve.modifiers, _fp.MAX_MODIFIERS, 'curve modifiers'))
        self.put(('MODIFIERS', len(modifiers)))
        for modifier in modifiers:
            self.settings(modifier, exclude=_fp._MODIFIER_UI)
        group = getattr(curve, 'group', None)
        self.put(('GROUP', group is not None))
        if group is not None:
            self.settings(group, exclude=_GROUP_EXCLUDE)


def _curve_key(curve):
    match = _PATH.fullmatch(curve.data_path)
    return (ast.literal_eval(match[1]), match[2], int(curve.array_index)) if match else None


def _finish(writer, diagnostics):
    if diagnostics is not None:
        diagnostics.update(writer.stats, points=writer.points, curves=writer.curves, policy=POLICY)
    return writer.hash.hexdigest()


def action_digest(action, handle, *, force_scalar=False, diagnostics=None):
    """Complete selected-slot motion proof; raises on any unproven content."""
    _fp._check_action_point_budget(_fp._selected_curves(action, handle))
    writer = _Writer(force_scalar=force_scalar)
    writer.put(('ACTION', 'OBJECT'))
    writer.settings(action, exclude=_ACTION_EXCLUDE)
    found = False
    if not action.is_action_layered:
        writer.put(('LAYER', {}))
        writer.put(('STRIP', {'type': 'LEGACY'}))
        for curve in _bounded(action.fcurves, _fp.MAX_CURVES, 'legacy curves'):
            writer.curve(curve)
        found = True
    else:
        for layer in _bounded(action.layers, _fp.MAX_LAYERS, 'Action layers'):
            strips = []
            for strip in _bounded(layer.strips, _fp.MAX_STRIPS, 'Action strips'):
                _require(strip.type == 'KEYFRAME', 'Unsupported Action strip type: ' + str(strip.type))
                bags = [bag for bag in _bounded(strip.channelbags, _fp.MAX_CHANNELBAGS, 'strip channelbags')
                        if bag.slot_handle == handle]
                _require(len(bags) <= 1, 'Several channelbags use the same selected slot in one strip.')
                if bags:
                    strips.append((strip, bags[0]))
            if strips:
                writer.put(('LAYER',))
                writer.settings(layer, exclude={'name', 'strips'})
                writer.put(('STRIPS', len(strips)))
                for strip, bag in strips:
                    writer.put(('STRIP',))
                    writer.settings(strip, exclude={'channelbags'})
                    for curve in _bounded(bag.fcurves, _fp.MAX_CURVES, 'strip curves'):
                        writer.curve(curve)
                    writer.put(('END_STRIP',))
                found = True
    _require(found, 'The selected slot has no supported curve content.')
    return _finish(writer, diagnostics)


def unaffected_digest(action, slot, affected, *, force_scalar=False, diagnostics=None):
    """Every untouched curve in all slots plus stable layer/strip structure.

    Derived Action frame ranges, affected-curve counts and newly owned wrist
    groups are deliberately outside this unchanged-content proof. The full
    action_digest separately authenticates each complete transaction snapshot.
    """
    _fp._slot(action, slot)
    _require(type(affected) in (set, frozenset) and all(type(v) is tuple and len(v) == 3
        and type(v[0]) is str and type(v[1]) is str and type(v[2]) is int and v[2] >= 0 for v in affected),
        'Affected wrist channels need exact bone/property/index triples.')
    writer = _Writer(force_scalar=force_scalar)
    writer.put(('UNAFFECTED',))
    if not action.is_action_layered:
        for curve in _bounded(action.fcurves, _fp.MAX_CURVES, 'legacy curves'):
            if _curve_key(curve) not in affected:
                writer.curve(curve)
        return _finish(writer, diagnostics)
    for layer in _bounded(action.layers, _fp.MAX_LAYERS, 'Action layers'):
        writer.put(('LAYER',))
        writer.settings(layer, exclude={'name', 'strips'})
        strips = tuple(_bounded(layer.strips, _fp.MAX_STRIPS, 'Action strips'))
        writer.put(('STRIPS', len(strips)))
        for strip in strips:
            _require(strip.type == 'KEYFRAME', 'Unsupported Action strip type: ' + str(strip.type))
            writer.put(('STRIP',))
            writer.settings(strip, exclude={'channelbags'})
            bags = tuple(_bounded(strip.channelbags, _fp.MAX_CHANNELBAGS, 'strip channelbags'))
            writer.put(('BAGS', len(bags)))
            for bag in bags:
                writer.put(('BAG', bag.slot_handle))
                for curve in _bounded(bag.fcurves, _fp.MAX_CURVES, 'strip curves'):
                    if bag.slot_handle != slot or _curve_key(curve) not in affected:
                        writer.curve(curve)
                writer.put(('END_BAG',))
    return _finish(writer, diagnostics)


class _ValidatedPoints(_fp._StreamSequence):
    def __init__(self, values, *, simple):
        super().__init__(values)
        self.simple = simple


def _legacy_points(collection, writer, exclude):
    """Recreate original point dictionaries from complete validated columns.

    Only the known simple native RNA shape skips the serializer's second
    _plain traversal. New/complex schemas retain the original scalar reader and
    original chunk validation, including its aggregate depth/item budget.
    """
    count = len(collection)
    _require(count <= _fp.MAX_CURVE_POINTS, 'Fingerprint collection exceeds its bound: curve points')
    if not count:
        return _ValidatedPoints((), simple=True)
    first = collection[0]
    plan = writer._plan(first, exclude)
    columns = []
    total_bytes = 0
    per_point_items = 1
    eligible = writer._native_points(first) and not writer.force_scalar
    for prop, name, kind, is_array, dimensions, width in plan:
        if sum(bool(d) for d in dimensions) > 1 or type(width) is not int or width < 1:
            eligible = False
        per_point_items += 1 + (width if is_array else 0)
        if kind in {'FLOAT', 'INT', 'BOOLEAN'}:
            total_bytes += count * width * {'FLOAT':8, 'INT':4, 'BOOLEAN':1}[kind]
        elif kind == 'ENUM' and not is_array and not getattr(prop, 'is_enum_flag', False):
            total_bytes += count * 4
        else:
            eligible = False
    _require(per_point_items <= _fp.MAX_ITEMS, 'Fingerprint payload exceeds its bounded size/depth.')
    if total_bytes > MAX_BUFFER_BYTES:
        eligible = False
    if eligible:
        for prop, name, kind, is_array, dimensions, width in plan:
            if kind == 'ENUM':
                table = tuple((item.identifier,item.value) for item in prop.enum_items if item.identifier)
                if (not table or len({n for n,_ in table}) != len(table)
                        or len({v for _,v in table}) != len(table)
                        or any(type(v) is not int or not -(2**31) <= v < 2**31 for _,v in table)):
                    eligible = False
                    break
                identifiers = {value:name for name,value in table}
                values = array('i',[0]) * count
                try:
                    collection.foreach_get(name,values)
                except (AttributeError,ReferenceError,RuntimeError,TypeError,ValueError,OverflowError):
                    eligible = False
                    break
                _enum_codes(values, identifiers, name)
                checks = (0,count-1) if count>1 else (0,)
                if any(identifiers[values[i]] != getattr(collection[i],name) for i in checks):
                    eligible = False
                    break
                columns.append((name,kind,False,1,values,identifiers))
            else:
                code = {'FLOAT':'d','INT':'i','BOOLEAN':'b'}[kind]
                values = array(code,[0]) * (count*width)
                try:
                    collection.foreach_get(name,values)
                    checks = (0,count-1) if count>1 else (0,)
                    matched = all(tuple(values[i*width:(i+1)*width]) == (
                        tuple(_flatten(getattr(collection[i],name))) if is_array else (getattr(collection[i],name),))
                        for i in checks)
                except (AttributeError,ReferenceError,RuntimeError,TypeError,ValueError,OverflowError):
                    matched = False
                if not matched:
                    eligible = False
                    break
                if kind == 'FLOAT':
                    _finite_numbers(values)
                elif kind == 'BOOLEAN':
                    _require(all(v in (0,1) for v in values),'Unknown native Boolean point value: '+name)
                columns.append((name,kind,is_array,width,values,None))
    if not eligible:
        columns.clear()
        return _ValidatedPoints((_fp._rna_settings(point, exclude=exclude, schema_cache=writer.schema_cache)
            for point in _bounded(collection,_fp.MAX_CURVE_POINTS,'curve points')),simple=False)
    writer.stats['bulk_fields'] += len(columns)
    writer.stats['max_buffer_bytes'] = max(writer.stats['max_buffer_bytes'],total_bytes)

    def rows():
        for index in range(count):
            result = {}
            for name,kind,is_array,width,values,identifiers in columns:
                if kind == 'ENUM':
                    value = identifiers[values[index]]
                elif is_array:
                    value = list(values[index*width:(index+1)*width])
                    if kind == 'BOOLEAN':
                        value = [bool(v) for v in value]
                else:
                    value = bool(values[index]) if kind == 'BOOLEAN' else values[index]
                result[name] = value
            yield result
    return _ValidatedPoints(rows(),simple=True)


def _legacy_curve(curve,writer):
    _require(getattr(curve,'driver',None) is None and not getattr(curve,'is_driver',False),
             'FCurve drivers are outside the plain FK content proof.')
    result = _fp._rna_settings(curve,exclude=_CURVE_EXCLUDE,schema_cache=writer.schema_cache)
    result['keyframe_points'] = _legacy_points(curve.keyframe_points,writer,_fp._KEY_UI)
    result['sampled_points'] = _legacy_points(curve.sampled_points,writer,{'select'})
    result['modifiers'] = _fp._StreamSequence(_fp._rna_settings(modifier,exclude=_fp._MODIFIER_UI,
        schema_cache=writer.schema_cache) for modifier in _bounded(curve.modifiers,_fp.MAX_MODIFIERS,'curve modifiers'))
    if getattr(curve,'group',None) is not None:
        result['group'] = _fp._rna_settings(curve.group,exclude=_GROUP_EXCLUDE,schema_cache=writer.schema_cache)
    return result


def _legacy_view(action,handle,writer):
    settings = _fp._rna_settings(action,exclude=_ACTION_EXCLUDE,schema_cache=writer.schema_cache)

    def curves(values):
        return _fp._StreamSequence(_legacy_curve(curve,writer)
            for curve in _bounded(values,_fp.MAX_CURVES,'strip curves'))

    def layers():
        found = False
        if action.is_action_layered:
            for layer in _bounded(action.layers,_fp.MAX_LAYERS,'Action layers'):
                strips = []
                for strip in _bounded(layer.strips,_fp.MAX_STRIPS,'Action strips'):
                    _require(strip.type=='KEYFRAME','Unsupported Action strip type: '+str(strip.type))
                    bags = [bag for bag in _bounded(strip.channelbags,_fp.MAX_CHANNELBAGS,'strip channelbags')
                            if bag.slot_handle==handle]
                    _require(len(bags)<=1,'Several channelbags use the same selected slot in one strip.')
                    if bags:
                        strips.append({'settings':_fp._rna_settings(strip,exclude={'channelbags'},
                            schema_cache=writer.schema_cache),'curves':curves(bags[0].fcurves)})
                if strips:
                    found = True
                    yield {'settings':_fp._rna_settings(layer,exclude={'name','strips'},
                        schema_cache=writer.schema_cache),'strips':strips}
        else:
            found = True
            yield {'settings':{},'strips':[{'settings':{'type':'LEGACY'},'curves':curves(action.fcurves)}]}
        _require(found,'The selected slot has no supported curve content.')
    return {'settings':settings,'slot_type':'OBJECT','layers':_fp._StreamSequence(layers())}


def _legacy_digest(value):
    digest = hashlib.sha256()
    point_count = 0

    def write(item,depth=0,field=''):
        nonlocal point_count
        _require(depth<=_fp.MAX_DEPTH,'Fingerprint payload exceeds its bounded depth.')
        if type(item) is dict:
            _require(len(item)<=_fp.MAX_ITEMS and all(type(k) is str for k in item),
                     'Fingerprint dictionary exceeds its bound or has non-string keys.')
            digest.update(b'{')
            for index,key in enumerate(sorted(item)):
                if index:
                    digest.update(b',')
                digest.update(_fp.canonical_bytes(key));digest.update(b':')
                write(item[key],depth+1,key)
            digest.update(b'}')
        elif type(item) in (list,tuple) or isinstance(item,_fp._StreamSequence):
            values = item.values if isinstance(item,_fp._StreamSequence) else item
            simple = isinstance(item,_ValidatedPoints) and item.simple
            digest.update(b'[')
            for index,child in enumerate(_bounded(values,_fp._SEQUENCE_LIMITS.get(field,_fp.MAX_ITEMS),field)):
                if field in {'keyframe_points','sampled_points'}:
                    point_count += 1
                    _require(point_count<=_fp.MAX_ACTION_POINTS,'Action exceeds its total key/sample point bound.')
                if index:
                    digest.update(b',')
                if field in _fp._CHUNK_SEQUENCES:
                    if simple:
                        # Only complete primitive/simple-array native columns
                        # enter here. Their widest shape is dict->list->scalar.
                        _require(depth+3<=_fp.MAX_DEPTH,'Fingerprint payload exceeds its bounded depth.')
                        chunk = child
                    else:
                        chunk = _fp._plain(child,depth=depth+1)
                    digest.update(json.dumps(chunk,ensure_ascii=False,allow_nan=False,
                        sort_keys=True,separators=(',',':')).encode('utf8'))
                else:
                    write(child,depth+1)
            digest.update(b']')
        else:
            digest.update(_fp.canonical_bytes(item))
    write(value)
    return digest.hexdigest()


def legacy_action_hash(action,handle,*,force_scalar=False,diagnostics=None):
    """Original persisted action_fingerprint bytes with fresh bulk native reads."""
    _fp._check_action_point_budget(_fp._selected_curves(action,handle))
    writer = _Writer(force_scalar=force_scalar)
    result = _legacy_digest({'schema':_fp.SCHEMA,'action':_legacy_view(action,handle,writer)})
    if diagnostics is not None:
        diagnostics.update(writer.stats,policy=_fp.PROOF_POLICY,byte_compatible_legacy=True)
    return result
