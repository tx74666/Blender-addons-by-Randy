"""Read-only ownership proof for temporary Natural Pose Dress spline pauses.

Only the generated, shared Manual Dress graph (physics=None) is supported.
No native field, solver, Original session or source record is written here.
The caller may mute only plan()['splines'] after authenticating its Natural
scratch owner. DEF relations remain enabled and retain their original mutes.
"""
import hashlib
import json
import math

import bpy


_STATE = 'natural_dress'
_PREFIX = 'natural_dress_'
_RNA_UI = {'rna_type', 'show_expanded', 'select', 'active', 'hide', 'lock', 'color', 'color_mode', 'group'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_encoded(value).encode('utf8')).hexdigest()


def _raw_digest(value):
    _require(isinstance(value, str), 'The Dress source record is unreadable; preserve its recovery.')
    return hashlib.sha256(value.encode('utf8')).hexdigest()


def _plain(value, identities):
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        _require(math.isfinite(value), 'A Dress graph field is not finite.')
        return value
    if isinstance(value, bpy.types.ID):
        matches = [name for name, target in identities.items() if value == target]
        _require(len(matches) == 1, 'The Dress graph has an unowned or aliased native target.')
        return {'id_ref': matches[0]}
    if isinstance(value, (set, frozenset)):
        return [_plain(item, identities) for item in sorted(value)]
    _require(not hasattr(value, 'bl_rna'), 'An unsupported Dress native pointer was preserved.')
    try:
        return [_plain(item, identities) for item in value]
    except TypeError as error:
        raise ValueError('An unsupported Dress graph field was preserved: ' + type(value).__name__) from error


def _rna(value, identities, omit=()):
    result = {}
    for prop in value.bl_rna.properties:
        name = prop.identifier
        if name in _RNA_UI or name in omit or prop.is_readonly or prop.type == 'COLLECTION':
            continue
        _require(prop.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM', 'POINTER'},
                 'An unsupported Dress RNA property was preserved: ' + name)
        result[name] = _plain(getattr(value, name), identities)
    return result


def _bindings(rig):
    animation = rig.animation_data
    if animation is None:
        return
    if animation.action is not None:
        yield animation.action, getattr(animation, 'action_slot', None)
    pending = [strip for track in animation.nla_tracks for strip in track.strips]
    while pending:
        strip = pending.pop()
        if strip.action is not None:
            yield strip.action, getattr(strip, 'action_slot', None)
        pending.extend(getattr(strip, 'strips', ()))


def _curves(action, slot):
    if getattr(action, 'is_action_legacy', False):
        if hasattr(action, 'fcurves'):
            yield from action.fcurves
        else:
            _require(getattr(action, 'is_empty', None) is True and not action.layers and not action.slots,
                     'A legacy Dress Action has no readable native constraint curves.')
        return
    if hasattr(action, 'layers'):
        # Unknown legacy NLA slot metadata is not permission to ignore curves.
        # Scan all bags for forbidden constraint paths; pure transform keys
        # remain harmless because the Natural caller isolates the binding.
        handle = int(slot.handle) if slot is not None else None
        for layer in action.layers:
            for strip in layer.strips:
                _require(hasattr(strip, 'channelbags'), 'An unsupported Dress Action strip was preserved.')
                bags = [bag for bag in strip.channelbags if handle is None or int(bag.slot_handle) == handle]
                _require(handle is None or len(bags) <= 1, 'The Dress Action has duplicated native slot data.')
                for bag in bags:
                    yield from bag.fcurves
    else:
        yield from action.fcurves


def _driver_contract(curve, identities):
    driver = curve.driver
    return {'curve': _rna(curve, identities), 'driver': _rna(driver, identities),
            'points': [_rna(point, identities) for point in curve.keyframe_points],
            'variables': [{'fields': _rna(variable, identities),
                           'targets': [_rna(target, identities) for target in variable.targets]}
                          for variable in driver.variables]}


def _manual(source, rig, record, index, identities):
    from . import unity_animation_dress as native
    wire = native._owned_wire(source, rig, record, index)
    _require(not (wire.library or wire.override_library or wire.data.library or wire.data.override_library),
             'Natural Dress needs an owned local wire and datablock.')
    chain = record['chains'][index]
    terminal = chain['manual'][-1]
    con = None
    for name in chain['manual']:
        constraints = tuple(rig.pose.bones[name].constraints)
        if name != terminal:
            _require(not constraints, 'A Dress Manual bone has an extra constraint: ' + name)
            continue
        _require(len(constraints) == 1, 'The Dress Manual spline is missing or has extra constraints.')
        con = constraints[0]
        _require(con.name == 'Skirt manual wire' and con.type == 'SPLINE_IK'
                 and con.target == wire and type(con.mute) is bool and con.influence == 1.
                 and con.chain_count == record['segment_count'] and con.use_even_divisions
                 and not con.use_curve_radius and not con.use_chain_offset and con.use_original_scale
                 and con.y_scale_mode == 'FIT_CURVE' and con.xz_scale_mode == 'NONE'
                 and con.owner_space == con.target_space == 'WORLD',
                 'The exact generated Dress Manual spline settings changed.')
    identities['wire_' + str(index)] = wire
    _require(wire.data.get('character_designer_skirt_owner') == record['owner'],
             'The Dress wire datablock ownership changed.')
    contract = {'object_name': wire.name_full, 'data_name': wire.data.name_full,
                'parent_type': wire.parent_type, 'parent_bone': wire.parent_bone,
                'parent_inverse': _plain(wire.matrix_parent_inverse, identities),
                'basis': _plain(wire.matrix_basis, identities),
                'data': _rna(wire.data, identities),
                'spline': _rna(wire.data.splines[0], identities),
                'points': [_rna(point, identities) for point in wire.data.splines[0].points],
                'hooks': [_rna(modifier, identities) for modifier in wire.modifiers],
                # Blender exposes enabled as the inverse writable alias of
                # mute. Both follow this owned temporary spline pause.
                'constraint': _rna(con, identities, {'mute', 'enabled'})}
    return wire, (terminal, con.name), contract


def _original_entry(rig, record):
    from . import body_original_mode as original, skirt_original_mode as dress
    if not original.active(rig):
        return None
    saved = json.loads(rig[original.SESSION])
    if 'dress_edit' not in saved:
        return None  # A legacy Original owner is not migrated or rewritten.
    entries = saved['dress_edit']
    _require(isinstance(entries, list), 'The Original Dress checkpoint is unreadable.')
    matches = [entry for entry in entries if entry.get('owner') == record['owner']]
    _require(len(matches) == 1, 'The shared Dress has no exact Original checkpoint.')
    entry = matches[0]
    names = {name for chain in record['chains'] for name in chain['def']} | {record['controls']['waist']}
    _require(set(entry['names']) == names and entry['rest'] == dress._rest(rig, names)
             and entry['parents'] == {name: rig.data.bones[name].parent.name for name in names}
             and entry.get('surface_installation') is None,
             'The Original Dress Rest, hierarchy or installation changed.')
    return entry


def _deform(source, rig, record, original, identities):
    from . import skirt_rig as skirt, skirt_original_mode as dress, unity_animation_dress as native
    corrections = dress._corrections(source, record)
    original_entry = _original_entry(rig, record)
    _holder, driver_id, influence_path = skirt.physics_control(source)
    animation = rig.animation_data
    curves = tuple(animation.drivers) if animation is not None else ()
    accepted, drivers, contract = set(), set(), []
    for chain in record['chains']:
        for name, manual, physics in zip(chain['def'], chain['manual'], chain['phys']):
            pb = rig.pose.bones[name]
            constraints = tuple(pb.constraints)
            _require(len(constraints) == 2, 'A Dress deform bone has missing or extra constraints: ' + name)
            copy, rotation = constraints
            modes = {'BEFORE_FULL'} if original_entry is not None else {'BEFORE_FULL' if name in corrections['bones'] else 'REPLACE'}
            _require(copy.name == 'Skirt manual pose' and copy.type == 'COPY_TRANSFORMS'
                     and rotation.name == 'Skirt physics delta' and rotation.type == 'COPY_ROTATION'
                     and copy.target == rotation.target == rig and copy.subtarget == manual
                     and rotation.subtarget == physics and copy.owner_space == copy.target_space
                     == rotation.owner_space == rotation.target_space == 'LOCAL'
                     and copy.mix_mode in modes and not copy.remove_target_shear and copy.influence == 1.
                     and rotation.mix_mode == 'BEFORE' and rotation.euler_order == 'AUTO'
                     and all((rotation.use_x, rotation.use_y, rotation.use_z))
                     and not any((rotation.invert_x, rotation.invert_y, rotation.invert_z))
                     and type(copy.mute) is bool and type(rotation.mute) is bool
                     and abs(getattr(copy, 'head_tail', 0.)) <= 1.e-7
                     and not getattr(copy, 'use_bbone_shape', False),
                     'The exact generated Dress deform relation changed: ' + name)
            if original_entry is not None:
                saved = original_entry['constraints'][name]
                _require(copy.mix_mode == 'BEFORE_FULL' and type(saved['manual_mute']) is bool
                         and type(saved['physics_mute']) is bool and copy.mute == saved['manual_mute']
                         and rotation.mute == saved['physics_mute'],
                         'An Original Dress DEF mute or mixing contract changed.')
            path = rotation.path_from_id('influence')
            matches = [curve for curve in curves if curve.data_path == path]
            _require(len(matches) == 1, 'A generated Dress influence driver is missing or duplicated.')
            curve = matches[0]
            _require(len(curve.keyframe_points) in (0, 2) and not curve.sampled_points,
                     'A generated Dress influence driver has custom key or sample data.')
            native._identity_driver_curve(curve)
            variables = tuple(curve.driver.variables)
            _require(not curve.sampled_points and curve.driver.type == 'AVERAGE'
                     and not curve.driver.use_self and len(variables) == 1
                     and variables[0].name == 'physics' and variables[0].type == 'SINGLE_PROP'
                     and len(variables[0].targets) == 1 and variables[0].targets[0].id == driver_id
                     and variables[0].targets[0].data_path == influence_path,
                     'The generated Dress influence driver was edited.')
            accepted.update(((name, copy.name), (name, rotation.name)))
            drivers.add((path, int(curve.array_index)))
            contract.append({'bone': name, 'manual': _rna(copy, identities),
                             'physics': _rna(rotation, identities, {'influence'}),
                             'driver': _driver_contract(curve, identities)})
    return accepted, drivers, {'corrections': corrections, 'relations': contract}


def _source(context, rig, source, owner, index, original):
    from . import skirt_rig as skirt, unity_animation_dress as native, skirt_wiggle
    _require(isinstance(source, bpy.types.Object) and source.type == 'MESH'
             and source.get(skirt.RIG_KEY) == rig and source.get(skirt.OWNER_KEY) == owner
             and not (source.library or source.override_library or source.data.library or source.data.override_library),
             'A shared Dress source was removed, linked or reassigned.')
    raw = source.get(skirt.RECORD_KEY)
    raw_sha = _raw_digest(raw)
    record = skirt.read_record(source)
    _require(record is not None and skirt.is_shared(record) and record['owner'] == owner,
             'The exact shared Dress record ownership changed.')
    _require(record.get('physics') is None,
             'Natural Pose currently supports Manual Dress without a Cloth physics backend.')
    count, segments = record['chain_count'], record['segment_count']
    _require(type(count) is int and 3 <= count <= 64 and count == len(record['chains']) == len(record['controls']['chains'])
             and type(segments) is int and 1 <= segments <= 32
             and all(len(chain[layer]) == segments for chain in record['chains'] for layer in ('manual', 'phys', 'def')),
             'The Dress chain inventory is incomplete.')
    names = [record['controls'][level] for level in ('waist', 'mid', 'hem')]
    names += [entry[level] for entry in record['controls']['chains'] for level in ('mid', 'hem')]
    names += [name for chain in record['chains'] for layer in ('manual', 'phys', 'def') for name in chain[layer]]
    wires = [chain['curve'] for chain in record['chains']]
    _require(all(isinstance(name, str) and name for name in names + wires)
             and len(names) == len(set(names)) and len(wires) == len(set(wires)),
             'The Dress scope has duplicated or aliased native bones/wires.')
    scope = set(names)
    control, deform, mechanism = skirt._bone_collection_layout(record)
    native._hierarchy(rig, record)
    native_deform = deform | {record['controls']['waist']}
    _require(all(rig.data.bones[name].use_deform == (name in native_deform) for name in scope),
             'A Dress bone changed its native deform role.')
    for name in scope - deform - {chain['manual'][-1] for chain in record['chains']}:
        _require(not rig.pose.bones[name].constraints, 'A Dress control or PHYS bone has an extra constraint: ' + name)
    motion = skirt_wiggle.preview_state(source)
    _require(not motion.get('running') and not motion.get('pending_recovery') and motion.get('stopped_confirmed'),
             'Pause or recover Dress Automatic physics before starting the static Rest test.')
    source_ref = _PREFIX + 'source_' + str(index)
    refs = {source_ref: source}
    identities = {'rig': rig, 'source': source}
    accepted, splines, wire_contracts, wire_refs = set(), set(), [], []
    for chain_index in range(count):
        wire, key, contract = _manual(source, rig, record, chain_index, identities)
        ref = _PREFIX + 'wire_' + str(index) + '_' + str(chain_index)
        _require(wire not in refs.values(), 'A Dress wire aliases another native source or wire.')
        refs[ref] = wire; wire_refs.append(ref)
        accepted.add(key); splines.add(key); wire_contracts.append(contract)
    deform_keys, drivers, deform_contract = _deform(source, rig, record, original, identities)
    accepted.update(deform_keys)
    prefixes = tuple(rig.pose.bones[name].path_from_id() for name in scope)
    for curve in rig.animation_data.drivers if rig.animation_data else ():
        if any(curve.data_path.startswith(prefix + '.') or curve.data_path.startswith(prefix + '[') for prefix in prefixes):
            _require((curve.data_path, int(curve.array_index)) in drivers,
                     'A custom driver controls the generated Dress input graph.')
    seen = set()
    for action, slot in _bindings(rig):
        key = (action.as_pointer(), int(slot.handle) if slot is not None else None)
        if key in seen:
            continue
        seen.add(key)
        for curve in _curves(action, slot):
            _require(not any(curve.data_path.startswith(prefix + '.constraints') for prefix in prefixes),
                     'A Dress constraint has authored animation; preserve it before static Rest testing.')
    bones = [{'name': name, 'parent': rig.data.bones[name].parent.name,
              'rest': _plain(rig.data.bones[name].matrix_local, identities)} for name in sorted(scope)]
    data = {'owner': owner, 'source_name': source.name_full, 'source_data_name': source.data.name_full,
            'record_sha256': raw_sha, 'scope': sorted(scope), 'source_ref': source_ref, 'wire_refs': wire_refs,
            'contract_sha256': _digest({'bones': bones, 'wires': wire_contracts, 'deform': deform_contract})}
    return scope, accepted, splines, data, refs


def plan(context, rig):
    """Fresh exact proof, independent of whether a Natural scratch is active."""
    from . import skirt_rig as skirt, body_original_mode as original
    try:
        _require(isinstance(rig, bpy.types.Object) and rig.type == 'ARMATURE'
                 and not (rig.library or rig.override_library or rig.data.library or rig.data.override_library),
                 'Natural Dress needs the local Main Rig.')
        raw = rig.get(skirt.SHARED_SOURCES_KEY, {})
        _require(hasattr(raw, 'items'), 'The shared Dress source registry is unreadable.')
        items = tuple(raw.items())
        _require(all(isinstance(owner, str) and owner for owner, source in items),
                 'A shared Dress owner key is invalid.')
        items = sorted(items, key=lambda entry: entry[0])
        seen, bones, accepted, splines, data, refs = set(), set(), set(), set(), [], {}
        for index, (owner, source) in enumerate(items):
            _require(isinstance(source, bpy.types.Object) and source not in seen,
                     'A shared Dress source is missing, duplicated or aliased.')
            seen.add(source)
            scope, keys, paused, manifest, native_refs = _source(context, rig, source, owner, index, original.active(rig))
            _require(not bones.intersection(scope), 'Two Dress sources claim the same native bone.')
            bones.update(scope); accepted.update(keys); splines.update(paused)
            data.append(manifest); refs.update(native_refs)
        actual = {bone.name for bone in rig.data.bones if bone.get(skirt.OWNER_KEY)}
        _require(actual == bones, 'The shared Dress registry omits or reassigns owned bones.')
        return {'constraint_keys': frozenset(accepted), 'splines': frozenset(splines),
                'data': {'version': 1, 'sources': data}, 'refs': refs}
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
        raise ValueError('The saved shared Dress graph is incomplete; all recovery was preserved.') from error


def capture(context, rig, state, refs):
    """Extend detached Python snapshots only; caller updates reference_names."""
    _require(type(state) is dict and type(refs) is dict, 'Dress capture needs detached Python snapshot dictionaries.')
    current = plan(context, rig)
    _require(_STATE not in state or state[_STATE] == current['data'],
             'An existing Natural Dress proof cannot be replaced by a new graph.')
    existing = {name for name in refs if name.startswith(_PREFIX)}
    _require(not existing or existing == set(current['refs']) and all(refs[name] == value for name, value in current['refs'].items()),
             'An existing Natural Dress native reference changed.')
    _encoded(current['data'])
    state[_STATE] = current['data']; refs.update(current['refs'])
    return current


def validate(context, rig, state, refs):
    """Re-prove the live graph before restore; never recapture missing proof."""
    current = plan(context, rig)
    own_refs = {name for name in refs if name.startswith(_PREFIX)}
    if _STATE not in state:
        _require(not current['data']['sources'] and not own_refs,
                 'This Natural session has no saved Dress proof; its recovery was preserved.')
        return current
    _require(state[_STATE] == current['data'], 'The Natural Dress source record or graph changed; recovery was preserved.')
    _require(own_refs == set(current['refs']) and all(refs[name] == value for name, value in current['refs'].items()),
             'A Natural Dress source or wire native reference was replaced; recovery was preserved.')
    return current
