"""Cached Hair strand browser and explicit, pinned batch operations.

Draw reads WindowManager RNA only. Refresh and every operation obtain fresh
source proof; no update callback, timer, solver or implicit all-strands fallback.
"""
import json

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, PropertyGroup, UIList

GROUP_ITEMS = (
    ('FRONT', 'Front / Bangs', ''),
    ('SIDE', 'Side', ''),
    ('BACK', 'Back / Long', ''),
    ('UNASSIGNED', 'Unassigned', ''),
)
FILTER_ITEMS = (('ALL', 'All Categories', ''),) + GROUP_ITEMS


def _service():
    from . import hair_strand_browser
    return hair_strand_browser


def _hair():
    from . import hair_bones
    return hair_bones


def _entries():
    from . import hair_strand_entries
    return hair_strand_entries


def _state(context):
    value = getattr(context.window_manager, 'character_designer_hair_bones', None)
    if value is None:
        raise ValueError('The Hair strand list is unavailable.')
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _read_array(value):
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError('The cached Hair entries are unreadable; refresh the strand list.') from error
    if not isinstance(result, list):
        raise ValueError('The cached Hair entries are unreadable; refresh the strand list.')
    return result


def _member_identity(member):
    """Pair identity only: mutable counts/categories and bone names can change on Redo."""
    return {key: member.get(key) for key in
            ('signature', 'strand_id', 'side', 'pair_id', 'mirror_id',
             'mirror_signature', 'pair_proof')}


def _entry_identity(entry):
    return dict(key=entry['key'], signatures=list(entry['signatures']),
                member_count=entry['member_count'], pair_id=entry['pair_id'],
                members=[_member_identity(member) for member in entry['members']])


def _entry_scope(identity):
    """Physical unit membership survives a version's regenerated registry IDs."""
    return dict(signatures=identity['signatures'], member_count=identity['member_count'],
                members=[{key: member.get(key) for key in
                          ('signature', 'side', 'mirror_signature', 'pair_proof')}
                         for member in identity['members']])


def _cached_entry(item):
    members = _read_array(item.members_json)
    signatures = _read_array(item.signatures_json)
    if (not item.entry_key or item.member_count not in (1, 2)
            or len(members) != item.member_count or len(signatures) != item.member_count
            or any(not isinstance(member, dict) for member in members)
            or any(type(value) is not str or not value for value in signatures)
            or len(set(signatures)) != len(signatures)
            or [member.get('signature') for member in members] != signatures
            or item.signature != signatures[0]
            or item.strand_id != members[0].get('strand_id')):
        raise ValueError('The cached Hair entry membership changed; refresh the strand list.')
    if item.member_count == 2:
        first, second = members
        if (not item.pair_id or item.entry_key != 'pair:' + item.pair_id
                or any(member.get('pair_id') != item.pair_id for member in members)
                or {first.get('side'), second.get('side')} != {'L', 'R'}
                or first.get('mirror_id') != second.get('strand_id')
                or second.get('mirror_id') != first.get('strand_id')
                or first.get('mirror_signature') != second.get('signature')
                or second.get('mirror_signature') != first.get('signature')
                or first.get('pair_proof') != second.get('pair_proof')):
            raise ValueError('The cached Hair pair membership changed; refresh the strand list.')
    elif item.entry_key != 'strand:' + item.strand_id:
        raise ValueError('The cached Hair entry identity changed; refresh the strand list.')
    return dict(key=item.entry_key, signatures=signatures, members=members,
                member_count=item.member_count, pair_id=item.pair_id or None)


def _cached_identities(state):
    identities = [_entry_identity(_cached_entry(item)) for item in state.list_items]
    keys = [entry['key'] for entry in identities]
    signatures = [signature for entry in identities for signature in entry['signatures']]
    if len(set(keys)) != len(keys) or len(set(signatures)) != len(signatures):
        raise ValueError('The cached Hair entry membership overlaps; refresh the strand list.')
    return identities


def _checked(state):
    return tuple(signature for item in state.list_items if item.checked
                 for signature in _cached_entry(item)['signatures'])


def _visible(item, category):
    return category == 'ALL' or category in _read_array(item.groups_json)


def _strand_label(bone_label):
    """Compact known generated root names without changing native identity."""
    import re
    match = re.fullmatch(r'(Hair(?: .+)?) ([0-9]+)(?:\.[0-9]{2}| Segment [0-9]{2})(?:\.[LRC])?', bone_label)
    if match is None:
        return bone_label
    words = match.group(1).split(' ')
    while len(words) > 1 and words[0].casefold() == words[1].casefold() == 'hair':
        del words[1]
    return ' '.join(words) + ' ' + str(int(match.group(2)))


def _count_label(counts, sides):
    if len(counts) == 2:
        if counts[0] == counts[1]:
            return str(counts[0]) + ' bones'
        return ' / '.join(str(side) + ' ' + str(count) for side, count in zip(sides, counts)) + ' bones'
    return str(counts[0]) + ' bones'


def _cached_count_label(item):
    members = _read_array(item.members_json)
    return _count_label(_read_array(item.counts_json), [member['side'] for member in members])


def _display_labels(state):
    from . import hair_strand_labels
    return {entry['key']: entry['label'] for entry in hair_strand_labels.logical_labels(
        tuple(dict(key=item.entry_key, bone_label=item.bone_label) for item in state.list_items))}


def _motion_snapshot(context):
    motion = getattr(context.window_manager, 'character_designer_hair_motion', None)
    if motion is None:
        return None, {}
    from . import hair_motion_profiles
    names = ('strand_id', 'group', 'sync_mirror', 'depth_index') + tuple(hair_motion_profiles.RANGES)
    names += tuple('depth_' + key for key in ('position', 'recovery', 'damping', 'mass', 'gravity'))
    return motion, {name: getattr(motion, name) for name in names}


def _load_motion_selection(context, snapshot, signatures, active_key='', *, active_signature=''):
    state = _state(context)
    if not signatures:
        return
    active = next((item for item in state.list_items if item.entry_key == active_key), None)
    target_signatures = _cached_entry(active)['signatures'] if active else signatures
    row = next((row for row in snapshot['rows'] if active_signature in signatures
                and row['signature'] == active_signature), None)
    if row is None:
        row = next((row for row in snapshot['rows'] if row['signature'] in target_signatures), None)
    if row is None:
        return
    source = _resolve(snapshot['fingerprint']['source'])
    from . import hair_motion_profiles, hair_motion_ui, hair_strand_registry
    data = hair_strand_registry.read(source, validate=True)
    if (data is not None and getattr(context.window_manager, 'character_designer_hair_motion', None)
            and hair_motion_profiles.read(source, registry=data) is not None):
        hair_motion_ui._load(context, source, row['strand_id'], highlight=False, verified_registry=data)


def _import_pose_selection(context, source, snapshot):
    from . import hair_resegment_selection
    signatures = set(hair_resegment_selection.selected(context, source))
    state = _state(context)
    chosen = tuple(item.entry_key for item in state.list_items
                   if signatures.intersection(_cached_entry(item)['signatures']))
    old = tuple(item.checked for item in state.list_items), state.list_active_index, state.list_anchor_key, state.list_anchor_filter
    motion, saved_motion = _motion_snapshot(context)
    try:
        for item in state.list_items:
            item.checked = item.entry_key in chosen
        state.list_active_index = next((index for index, item in enumerate(state.list_items)
                                        if item.checked), state.list_active_index)
        active = getattr(context, 'active_pose_bone', None)
        active_row = next((row for row in snapshot['rows'] if active and active.name in row['bones']
                           and row['signature'] in signatures), None)
        active_key = next((item.entry_key for item in state.list_items if active_row
                           and active_row['signature'] in _cached_entry(item)['signatures']), '')
        if active_key:
            state.list_active_index = next(index for index, item in enumerate(state.list_items)
                                          if item.entry_key == active_key)
        _load_motion_selection(context, snapshot, _checked(state), active_key,
                               active_signature=active_row['signature'] if active_row else '')
        state.list_anchor_key = ''
        state.list_anchor_filter = ''
    except Exception:
        for item, selected in zip(state.list_items, old[0]):
            item.checked = selected
        state.list_active_index, state.list_anchor_key, state.list_anchor_filter = old[1:]
        for name, value in saved_motion.items():
            setattr(motion, name, value)
        raise


def navigate_ui(context, direction):
    """Navigate visible logical entries with the same proof and scope as a row click."""
    from types import SimpleNamespace
    source, armature, unused_signatures, snapshot = _authenticate(
        SimpleNamespace(action_pin=_capture(context)), context)
    if direction == 0:
        _import_pose_selection(context, source, snapshot)
        return {'FINISHED'}
    if direction not in (-1, 1):
        raise ValueError('Choose Previous, Next or Selected Hair strands.')
    state = _state(context)
    visible = tuple(item for item in state.list_items if _visible(item, state.list_filter))
    if not visible:
        raise ValueError('No Hair entries are visible in this category.')
    motion = getattr(context.window_manager, 'character_designer_hair_motion', None)
    current = next((index for index, item in enumerate(visible) if motion
                    and any(member['strand_id'] == motion.strand_id
                            for member in _cached_entry(item)['members'])), None)
    index = ((current + direction) % len(visible)) if current is not None else (0 if direction > 0 else len(visible) - 1)
    key = visible[index].entry_key
    _apply_list_selection(context, armature, snapshot, (key,), anchor=key, active_key=key)
    return {'FINISHED'}


def invalidate_ui(context):
    """Invalidate authorization without discarding cached rows or artist data."""
    _state(context).list_pin = ''


def refresh_ui(context, source=None, checked_signatures=None):
    """Explicit strict refresh; changes only transient list/motion UI settings."""
    state = _state(context)
    try:
        source = _hair()._source(context) if source is None else source
        snapshot = _service().strict_snapshot(source)
        rows = snapshot['rows']
        entries = _entries().build_entries(rows)
        old = json.loads(state.list_pin) if state.list_pin else {}
        same_source = old.get('source') == snapshot['fingerprint']['source']
        if checked_signatures is None:
            # Preserve checks only within this same source object/session.
            chosen = _checked(state) if same_source else ()
        else:
            if isinstance(checked_signatures, (str, bytes)):
                raise ValueError('The checked Hair strand list is unreadable.')
            chosen = tuple(checked_signatures)
        if any(type(signature) is not str for signature in chosen):
            raise ValueError('The checked Hair strand list is unreadable.')
        # A remembered member check becomes one whole logical entry. An unknown
        # member is stale scope, never an implicit all-strands request.
        chosen = set(_entries().normalize_signatures(entries, chosen))
        from . import hair_strand_registry as registry, hair_motion_profiles as profiles
        data = registry.read(source, validate=True)
        motion = getattr(context.window_manager, 'character_designer_hair_motion', None)
        # Existing profiles are loaded, never initialized or given a preset.
        if data is not None and motion is not None and profiles.read(source, registry=data) is not None:
            by_id = {item['strand_id'] for item in data['strands']}
            checked_ids = [row['strand_id'] for row in rows
                           if row['signature'] in chosen and row['strand_id'] in by_id]
            current = motion.strand_id
            target = current if current in by_id else (checked_ids or [rows[0]['strand_id']])[0]
            from . import hair_motion_ui
            hair_motion_ui._load(context, source, target, highlight=False, verified_registry=data)
        state.list_pin = ''
        state.list_items.clear()
        for row in entries:
            item = state.list_items.add()
            for key in ('signature', 'strand_id', 'bone_label', 'group', 'side'):
                setattr(item, key, row[key])
            for key in ('pair_id', 'mirror_id', 'mirror_signature'):
                setattr(item, key, row.get(key) or '')
            item.bones_json = _json(row['bones'])
            item.entry_key = row['key']
            item.members_json = _json(row['members'])
            item.signatures_json = _json(row['signatures'])
            item.groups_json = _json(row['groups'])
            item.counts_json = _json(row['counts'])
            item.member_count = row['member_count']
            item.count = row['count'] if row['count'] is not None else 0
            item.order = row['order']
            item.sync_mirror = all(member.get('sync_mirror', False) for member in row['members'])
            item.checked = bool(chosen.intersection(row['signatures']))
        state.list_active_index = min(state.list_active_index, max(0, len(entries) - 1))
        if not same_source or state.list_anchor_key not in {entry['key'] for entry in entries}:
            state.list_anchor_key = ''
            state.list_anchor_filter = ''
        from . import hair_bones_rig
        armature = source.get(hair_bones_rig.RIG_KEY)
        state.list_pose_position = armature.data.pose_position
        state.source = source
        state.list_pin = _json(snapshot['fingerprint'])
        return snapshot
    except Exception:
        invalidate_ui(context)
        raise


def _capture(context):
    state = _state(context)
    if not state.list_pin:
        raise ValueError('Refresh the Hair strand list before using it.')
    fingerprint = json.loads(state.list_pin)
    signatures = _checked(state)
    identities = _cached_identities(state)
    checked_keys = {item.entry_key for item in state.list_items if item.checked}
    labels = _display_labels(state)
    summary = [dict(key=item.entry_key, bone_label=item.bone_label, display_label=labels[item.entry_key], count=item.count,
                    counts=_read_array(item.counts_json),
                    sides=[member['side'] for member in _read_array(item.members_json)],
                    member_count=item.member_count)
               for item in state.list_items if item.checked]
    return _json(dict(fingerprint=fingerprint, signatures=signatures, summary=summary,
                      entries=[entry for entry in identities if entry['key'] in checked_keys]))


def _resolve(identity):
    if (not isinstance(identity, dict) or type(identity.get('name')) is not str
            or not identity['name'] or type(identity.get('session_uid')) is not str
            or not identity['session_uid']):
        raise ValueError('The saved Hair identity is unreadable; refresh its strand list.')
    obj = bpy.data.objects.get(identity['name'])
    if obj is None or str(obj.session_uid) != identity['session_uid']:
        raise ValueError('The Hair object was renamed or replaced; refresh its strand list.')
    return obj


def _authenticate(operator, context, *, require_checked=False):
    if not operator.action_pin:
        operator.action_pin = _capture(context)
    try:
        pin = json.loads(operator.action_pin)
        fingerprint = pin['fingerprint']
        source = _resolve(fingerprint['source'])
        rig = _resolve(fingerprint['rig'])
        if not isinstance(pin['signatures'], list):
            raise ValueError('The saved checked Hair strands are unreadable.')
        signatures = tuple(pin['signatures'])
        if (any(type(value) is not str or not value for value in signatures)
                or len(set(signatures)) != len(signatures)):
            raise ValueError('The saved checked Hair strands are unreadable.')
        captured_entries = pin['entries']
        if not isinstance(captured_entries, list):
            raise ValueError('The saved Hair entry membership is unreadable.')
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('The saved Hair list action is unreadable; refresh its strand list.') from error
    if _hair()._source(context) is not source:
        raise ValueError('The active Hair changed; refresh its strand list and choose again.')
    snapshot = _service().strict_snapshot(source)
    if snapshot['fingerprint'] != fingerprint:
        raise ValueError('The Hair binding or settings changed; refresh its strand list and choose again.')
    # Native Redo restores source data, but WindowManager cache is not undone.
    # Compare the user's scope, never require that cache's latest version pin.
    state = _state(context)
    if set(_checked(state)) != set(signatures):
        raise ValueError('The checked Hair strands changed; choose this action again.')
    entries = _entries().build_entries(snapshot['rows'])
    actual_units = [_entry_identity(entry) for entry in entries]
    # Membership is authenticated from fresh physical rows. Cache counts and
    # categories deliberately remain usable after native Undo restores source
    # data, while this WindowManager cache still describes the later state.
    if ([_entry_scope(entry) for entry in _cached_identities(state)] !=
            [_entry_scope(entry) for entry in actual_units]):
        raise ValueError('The Hair entry membership changed; refresh the strand list.')
    expanded = _entries().normalize_signatures(entries, signatures)
    if set(expanded) != set(signatures):
        raise ValueError('Choose the complete Hair pair; partial entry scope is refused.')
    expected_entries = [entry for entry in actual_units
                        if set(entry['signatures']).intersection(signatures)]
    if captured_entries != expected_entries:
        raise ValueError('The saved Hair entry membership changed; choose this action again.')
    signatures = expanded
    if require_checked and not signatures:
        raise ValueError('Check at least one Hair strand; an empty list never means all strands.')
    return source, rig, signatures, snapshot


def _failure(operator, context, error):
    _state(context).last_message = str(error)
    operator.report({'ERROR'}, str(error))
    return {'CANCELLED'}


def _after_commit(operator, context, source, signatures, *, bone_count=None):
    try:
        state = _state(context)
        state.source = source
        if bone_count is not None:
            state.bone_count = bone_count
        refresh_ui(context, source, checked_signatures=signatures)
    except Exception as error:
        try:
            invalidate_ui(context)
        except Exception:
            pass
        operator.report({'WARNING'}, 'Hair changed; refresh the strand list. ' + str(error))


def _selection_context(context, armature):
    """Only capture state touched by a list selection; no mesh/author scans."""
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Use Object or Pose Mode to select Hair strands from the list.')
    if context.mode == 'POSE' and any(obj is not armature for obj in context.objects_in_mode):
        raise ValueError('Use the bound Hair rig alone in Pose Mode to select its strands.')
    active = context.view_layer.objects.active
    arms = {obj for obj in (active, armature) if obj and obj.type == 'ARMATURE'}
    return dict(active=active, mode=active.mode if active else 'OBJECT',
                selected=tuple(obj for obj in context.view_layer.objects if obj.select_get()),
                arms={obj: dict(active=obj.data.bones.active,
                    selected={p.name: bool(p.select) for p in obj.pose.bones},
                    hidden={b.name: bool(b.hide) for b in obj.data.bones},
                    collections={c.name: bool(c.is_visible) for c in obj.data.collections_all},
                    in_front=obj.show_in_front) for obj in arms})


def _restore_selection_context(context, saved):
    active = context.view_layer.objects.active
    needs_mode = active is not saved['active'] or (active and active.mode != saved['mode'])
    if needs_mode and active and active.mode != 'OBJECT':
        if bpy.ops.object.mode_set(mode='OBJECT') != {'FINISHED'}:
            raise RuntimeError('Could not restore the Hair selection mode.')
    for obj in context.view_layer.objects:
        obj.select_set(obj in saved['selected'])
    context.view_layer.objects.active = saved['active']
    if needs_mode and saved['active'] and saved['mode'] != 'OBJECT':
        if bpy.ops.object.mode_set(mode=saved['mode']) != {'FINISHED'}:
            raise RuntimeError('Could not restore the original Hair selection mode.')
    for obj, state in saved['arms'].items():
        obj.show_in_front = state['in_front']
        for pose in obj.pose.bones:
            pose.select = state['selected'][pose.name]
            pose.bone.hide = state['hidden'][pose.name]
        for collection in obj.data.collections_all:
            collection.is_visible = state['collections'][collection.name]
        obj.data.bones.active = state['active']


def _select_list_chains(context, armature, names, active_name=None):
    """Select an explicit scope, including empty, without repeated mode bounces."""
    if context.object is not armature or context.mode != 'POSE':
        from . import hair_bones_rig
        hair_bones_rig._mode(context, armature, 'POSE')
    wanted = set(names)
    for pose in armature.pose.bones:
        pose.select = pose.name in wanted
    if wanted:
        armature.show_in_front = True
    for name in names:
        bone = armature.data.bones[name]
        bone.hide = False
        for collection in bone.collections:
            collection.is_visible = True
    armature.data.bones.active = armature.data.bones.get(active_name or (names[0] if names else ''))


def _apply_list_selection(context, armature, snapshot, keys, *, anchor='', active_key=''):
    state = _state(context)
    chosen = set(keys)
    entries = [_cached_entry(item) for item in state.list_items if item.entry_key in chosen]
    signatures = {signature for entry in entries for signature in entry['signatures']}
    names = tuple(name for row in snapshot['rows'] if row['signature'] in signatures for name in row['bones'])
    target = next((entry for entry in entries if entry['key'] == active_key), None)
    active_name = next((row['bones'][0] for row in snapshot['rows']
                        if target and row['signature'] in target['signatures']), None)
    saved = _selection_context(context, armature)
    motion, saved_motion = _motion_snapshot(context)
    old = (tuple(item.checked for item in state.list_items), state.list_active_index,
           state.list_anchor_key, state.list_anchor_filter)
    try:
        _select_list_chains(context, armature, names, active_name)
        for index, item in enumerate(state.list_items):
            item.checked = item.entry_key in chosen
            if item.entry_key == active_key:
                state.list_active_index = index
        state.list_anchor_key = anchor
        state.list_anchor_filter = state.list_filter
        _load_motion_selection(context, snapshot, tuple(signatures), active_key if target else '')
    except Exception as error:
        for item, checked in zip(state.list_items, old[0]):
            item.checked = checked
        state.list_active_index, state.list_anchor_key, state.list_anchor_filter = old[1:]
        for name, value in saved_motion.items():
            setattr(motion, name, value)
        try:
            _restore_selection_context(context, saved)
        except Exception as recovery_error:
            raise RuntimeError(str(error) + '; selection recovery needs attention: ' + str(recovery_error)) from error
        raise


class CHARACTERDESIGNER_OT_hair_strand_list_click(Operator):
    bl_idname = 'character_designer.hair_strand_list_click'
    bl_label = 'Select Hair Strand'
    bl_description = 'Select full strands: Ctrl toggles, Shift selects a visible range, Ctrl+Shift adds a range'
    bl_options = {'UNDO'}
    entry_key: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    action_pin: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    filter_pin: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    extend: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    toggle: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        self.extend, self.toggle = event.shift, event.ctrl
        return self.execute(context)

    def execute(self, context):
        try:
            unused_source, armature, unused_signatures, snapshot = _authenticate(self, context)
            state = _state(context)
            if self.filter_pin != state.list_filter:
                raise ValueError('The category filter changed; select the Hair row again.')
            from . import hair_list_selection
            keys, anchor = hair_list_selection.plan(
                tuple(item.entry_key for item in state.list_items),
                tuple(item.entry_key for item in state.list_items if _visible(item, state.list_filter)),
                tuple(item.entry_key for item in state.list_items if item.checked),
                self.entry_key,
                state.list_anchor_key if state.list_anchor_filter == state.list_filter else '',
                extend=self.extend, toggle=self.toggle)
            _apply_list_selection(context, armature, snapshot, keys, anchor=anchor, active_key=self.entry_key)
            state.last_message = ''
            return {'FINISHED'}
        except Exception as error:
            return _failure(self, context, error)


class CharacterDesignerHairStrandListItem(PropertyGroup):
    # Keep the transient property/API for existing pinned actions and Redo.
    checked: BoolProperty(name='Selected', default=False, options={'SKIP_SAVE'})
    signature: StringProperty(options={'SKIP_SAVE'})
    strand_id: StringProperty(options={'SKIP_SAVE'})
    bone_label: StringProperty(options={'SKIP_SAVE'})
    bones_json: StringProperty(options={'SKIP_SAVE'})
    entry_key: StringProperty(options={'SKIP_SAVE'})
    members_json: StringProperty(options={'SKIP_SAVE'})
    signatures_json: StringProperty(options={'SKIP_SAVE'})
    groups_json: StringProperty(options={'SKIP_SAVE'})
    counts_json: StringProperty(options={'SKIP_SAVE'})
    member_count: IntProperty(options={'SKIP_SAVE'})
    group: StringProperty(options={'SKIP_SAVE'})
    count: IntProperty(options={'SKIP_SAVE'})
    side: StringProperty(options={'SKIP_SAVE'})
    order: IntProperty(options={'SKIP_SAVE'})
    pair_id: StringProperty(options={'SKIP_SAVE'})
    mirror_id: StringProperty(options={'SKIP_SAVE'})
    mirror_signature: StringProperty(options={'SKIP_SAVE'})
    sync_mirror: BoolProperty(options={'SKIP_SAVE'})


class CHARACTERDESIGNER_UL_hair_strand_list(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.operator_context = 'INVOKE_DEFAULT'
        try:
            pin = _capture(context)
            label = (_display_labels(data)[item.entry_key] + '   ·   ' +
                     _cached_count_label(item) + '   ·   ' + item.group.title() + ' / ' + item.side)
        except (ValueError, KeyError, TypeError):
            pin = ''
            label = 'Refresh strand list'
            row.enabled = False
        op = row.operator('character_designer.hair_strand_list_click',
                          text=label,
                          icon='BONE_DATA', depress=item.checked)
        op.entry_key = item.entry_key
        op.action_pin = pin
        op.filter_pin = data.list_filter

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        return ([self.bitflag_filter_item if _visible(item, data.list_filter) else 0
                 for item in items], [])


class CHARACTERDESIGNER_OT_hair_strand_list_refresh(Operator):
    bl_idname = 'character_designer.hair_strand_list_refresh'
    bl_label = 'Refresh Strand List'
    bl_description = 'Read current owned Hair strands and their counts without changing Hair'

    def execute(self, context):
        try:
            result = refresh_ui(context)
            _state(context).last_message = ('Refreshed ' + str(len(_state(context).list_items)) +
                ' Hair entries (' + str(len(result['rows'])) + ' physical chains).')
            return {'FINISHED'}
        except Exception as error:
            return _failure(self, context, error)


class CHARACTERDESIGNER_OT_hair_strand_list_check(Operator):
    bl_idname = 'character_designer.hair_strand_list_check'
    bl_label = 'Select Hair Strands'
    bl_description = 'Select list entries; Check Selected Bones keeps the viewport bone selection unchanged'
    action: EnumProperty(items=(
        ('SELECT_VISIBLE', 'Select Visible', ''),
        ('CLEAR', 'Deselect All', ''),
        ('IMPORT_POSE', 'Check Selected Bones', 'Check the strands containing the Hair bones currently selected in Pose Mode'),
    ))
    action_pin: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    filter_pin: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            source, armature, unused_signatures, snapshot = _authenticate(self, context)
            state = _state(context)
            if self.action == 'SELECT_VISIBLE':
                if self.filter_pin and self.filter_pin != state.list_filter:
                    raise ValueError('The category filter changed; choose Select Visible again.')
                keys = tuple(item.entry_key for item in state.list_items
                             if item.checked or _visible(item, state.list_filter))
                _apply_list_selection(context, armature, snapshot, keys)
            elif self.action == 'CLEAR':
                _apply_list_selection(context, armature, snapshot, ())
            elif self.action == 'IMPORT_POSE':
                _import_pose_selection(context, source, snapshot)
            else:
                raise ValueError('Choose a strand list check action.')
            return {'FINISHED'}
        except Exception as error:
            return _failure(self, context, error)


class CHARACTERDESIGNER_OT_hair_strand_list_select_pose(Operator):
    bl_idname = 'character_designer.hair_strand_list_select_pose'
    bl_label = 'Select Checked'
    bl_description = 'Select the complete checked chains in Pose Mode; keep the rig Rest/Pose display choice'
    bl_options = {'REGISTER', 'UNDO'}
    action_pin: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            unused_source, armature, signatures, snapshot = _authenticate(self, context, require_checked=True)
            names = [name for row in snapshot['rows'] if row['signature'] in signatures
                     for name in row['bones']]
            from . import hair_bones_rig
            hair_bones_rig._select_chains(context, armature, names)
            return {'FINISHED'}
        except Exception as error:
            return _failure(self, context, error)


class CHARACTERDESIGNER_OT_hair_strand_list_assign_category(Operator):
    bl_idname = 'character_designer.hair_strand_list_assign_category'
    bl_label = 'Assign Category'
    bl_description = 'Assign only the checked strands a category while preserving every physical value'
    bl_options = {'REGISTER', 'UNDO'}
    group: EnumProperty(name='Category', items=GROUP_ITEMS, default='UNASSIGNED')
    action_pin: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            source, unused_rig, signatures, snapshot = _authenticate(self, context, require_checked=True)
            _service().assign_category(context, source, signatures, self.group,
                                       expected_fingerprint=snapshot['fingerprint'])
        except Exception as error:
            return _failure(self, context, error)
        # The source transaction has committed. No cache/report failure may
        # return CANCELLED and discard its native Undo entry.
        _after_commit(self, context, source, signatures)
        self.report({'INFO'}, 'Assigned ' + self.group.title() + ' to ' + str(len(signatures)) + ' physical Hair chains.')
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_hair_strand_list_rebuild(Operator):
    bl_idname = 'character_designer.hair_strand_list_rebuild'
    bl_label = 'Rebuild Selected Strands'
    bl_description = 'Replace the selected strands with the new bone count on the current character'
    bl_options = {'REGISTER', 'UNDO'}
    bone_count: IntProperty(name='New Bones per Strand', default=6, min=1, max=12)
    action_pin: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        try:
            _authenticate(self, context, require_checked=True)
            if not self.is_property_set('bone_count'):
                self.bone_count = _state(context).bone_count
        except Exception as error:
            return _failure(self, context, error)
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        layout = self.layout
        try:
            pin = json.loads(self.action_pin)
            layout.label(text='Hair: ' + pin['fingerprint']['source']['name'], icon='OUTLINER_OB_MESH')
            layout.label(text=str(len(pin['summary'])) + ' selected entries (' +
                         str(len(pin['signatures'])) + ' physical chains)')
            for item in pin['summary'][:8]:
                paired = item['member_count'] == 2
                side = 'L/R' if paired else item['sides'][0]
                layout.label(text=item.get('display_label', _strand_label(item['bone_label'])) + ': ' +
                             _count_label(item['counts'], item['sides']) + ' → ' +
                             str(self.bone_count) + ' bones · ' + side)
            if len(pin['summary']) > 8:
                layout.label(text='… and ' + str(len(pin['summary']) - 8) + ' more entries')
        except (ValueError, KeyError, TypeError):
            layout.label(text='Refresh the Hair strand list and choose again.', icon='ERROR')
        layout.prop(self, 'bone_count')
        layout.label(text='Hidden selected entries and both paired chains are included.')
        layout.label(text='Replaces these strands; bending and motion are sampled.')

    def execute(self, context):
        try:
            source, unused_rig, signatures, unused_snapshot = _authenticate(self, context, require_checked=True)
            from . import hair_resegment
            # This explicit non-empty tuple must never become None (all Hair).
            result = hair_resegment.replace_selected(context, source, bone_count=self.bone_count,
                                           strand_signatures=tuple(signatures))
        except Exception as error:
            return _failure(self, context, error)
        _after_commit(self, context, result['mesh'], result['selected_signatures'],
                      bone_count=self.bone_count)
        if result.get('cleanup_warning'):
            self.report({'WARNING'}, result['cleanup_warning'])
        self.report({'INFO'}, 'Replaced ' + str(len(signatures)) + ' Hair strands with ' +
                    str(self.bone_count) + ' bones each.')
        return {'FINISHED'}


def draw_browser(layout, context, source, bound):
    """Render cached RNA only; native proof is explicit in Refresh/actions."""
    state = _state(context)
    box = layout.box()
    row = box.row(align=True)
    if not bound:
        row.label(text='Strands', icon='BONE_DATA')
        box.label(text='Bind Hair before using the strand list.', icon='INFO')
        return
    rows = tuple(state.list_items)
    try:
        visible = tuple(item for item in rows if _visible(item, state.list_filter))
        checked = _checked(state)
        pin = _capture(context) if state.list_pin else ''
        labels = _display_labels(state)
    except (ValueError, KeyError, TypeError):
        row.label(text='Strands', icon='BONE_DATA')
        row.operator('character_designer.hair_strand_list_refresh', text='', icon='FILE_REFRESH')
        box.label(text='Refresh the Hair strand list.', icon='ERROR')
        return
    title = 'Strands'
    motion = getattr(context.window_manager, 'character_designer_hair_motion', None)
    for item in rows:
        member = next((member for member in _cached_entry(item)['members']
                       if motion and member['strand_id'] == motion.strand_id), None)
        if member is not None:
            title += ' · ' + labels[item.entry_key]
            if item.member_count == 2:
                title += ' · ' + member['side']
            break
    row.label(text=title, icon='BONE_DATA')
    row.operator('character_designer.hair_strand_list_refresh', text='', icon='FILE_REFRESH')
    visible_checked = sum(item.checked for item in visible)
    checked_count = sum(item.checked for item in rows)
    column = box.column()
    column.enabled = bool(state.list_pin)
    navigation = column.row(align=True)
    navigation.operator('character_designer.hair_motion_navigate', text='Previous').direction = -1
    navigation.operator('character_designer.hair_motion_navigate', text='Next').direction = 1
    selected = navigation.operator('character_designer.hair_strand_list_check', text='Selected')
    selected.action = 'IMPORT_POSE'
    selected.action_pin = pin
    column.prop(state, 'list_filter', text='Category')
    column.template_list('CHARACTERDESIGNER_UL_hair_strand_list', '', state, 'list_items',
                         state, 'list_active_index', rows=8, maxrows=12)
    if not state.list_pin:
        box.label(text='Refresh to validate the current Hair source/version.', icon='INFO')
        return
    row = column.row(align=True)
    op = row.operator('character_designer.hair_strand_list_check', text='Select Visible')
    op.action, op.action_pin, op.filter_pin = 'SELECT_VISIBLE', pin, state.list_filter
    op = row.operator('character_designer.hair_strand_list_check', text='Deselect All')
    op.action, op.action_pin = 'CLEAR', pin
    actions = column.column()
    actions.enabled = bool(checked)
    count_row = actions.row(align=True)
    count_row.operator_context = 'EXEC_DEFAULT'
    count_row.prop(state, 'bone_count', text='Bones')
    op = count_row.operator('character_designer.hair_strand_list_rebuild', text='Apply', icon='CHECKMARK')
    op.action_pin, op.bone_count = pin, state.bone_count
    row = actions.row(align=True)
    row.prop(state, 'list_assign_group', text='')
    op = row.operator('character_designer.hair_strand_list_assign_category', text='Assign Category')
    op.action_pin, op.group = pin, state.list_assign_group
    if state.list_pose_position == 'REST':
        box.label(text='Use Pose Position to rebuild.', icon='INFO')
    if checked_count > visible_checked:
        box.label(text=str(checked_count - visible_checked) + ' selected entries hidden by category.', icon='INFO')


ITEM_CLASSES = (CharacterDesignerHairStrandListItem,)
CLASSES = (
    CHARACTERDESIGNER_UL_hair_strand_list,
    CHARACTERDESIGNER_OT_hair_strand_list_click,
    CHARACTERDESIGNER_OT_hair_strand_list_refresh,
    CHARACTERDESIGNER_OT_hair_strand_list_check,
    CHARACTERDESIGNER_OT_hair_strand_list_select_pose,
    CHARACTERDESIGNER_OT_hair_strand_list_assign_category,
    CHARACTERDESIGNER_OT_hair_strand_list_rebuild,
)
