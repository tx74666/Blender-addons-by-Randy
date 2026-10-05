"""Hair strand navigation and persistent motion settings in the Hair rig page."""
import json

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import hair_bones, hair_bones_rig, hair_motion_profiles as profiles
from . import hair_strand_registry as registry
from .ui_constants import SIDEBAR_CATEGORY, rig_page_active


def _state(context):
    return getattr(context.window_manager, 'character_designer_hair_motion', None)


def _source(context):
    return hair_bones._source(context)


def _backend():
    from . import hair_wiggle_adapter
    return hair_wiggle_adapter


def _load(context, source, strand_id, *, highlight=False, verified_registry=None):
    # Only navigation passes its fresh proof here, before any native mutation.
    # All other callers retain their final fresh read and rollback boundary.
    data = registry.read(source, validate=True) if verified_registry is None else verified_registry
    item = next((item for item in data['strands'] if item['strand_id'] == strand_id), None)
    if item is None:
        raise ValueError('Select an existing Hair strand first.')
    values = profiles.effective(source, strand_id, registry=data)
    settings = _state(context)
    settings.strand_id = strand_id
    settings.group = values['group']
    settings.sync_mirror = values['sync_mirror']
    for key in profiles.RANGES:
        setattr(settings, key, values[key])
    settings.depth_index = min(settings.depth_index, len(values['depth']) - 1)
    _load_depth(context, values)
    if highlight:
        armature = source.get(hair_bones_rig.RIG_KEY)
        hair_bones_rig._select_chains(context, armature, item['bones'])
    return data, values


def _load_depth(context, values):
    settings = _state(context)
    knot = values['depth'][settings.depth_index]
    for key in ('position', 'recovery', 'damping', 'mass', 'gravity'):
        setattr(settings, 'depth_' + key, knot[key])


def _operate(operator, context, function):
    settings = _state(context)
    try:
        result = function()
        if settings:
            settings.last_message = ''
        return result or {'FINISHED'}
    except (ValueError, RuntimeError) as exc:
        if settings:
            settings.last_message = str(exc)
        operator.report({'ERROR'}, str(exc))
        return {'CANCELLED'}


def _stop(context):
    backend = _backend()
    if backend.status(context).get('active'):
        backend.stop_preview(context)


class CharacterDesignerHairMotionState(PropertyGroup):
    strand_id: StringProperty(options={'SKIP_SAVE'})
    pair_left_id: StringProperty(options={'SKIP_SAVE'})
    show_depth: BoolProperty(name='Along Strand', default=False, options={'SKIP_SAVE'})
    group: EnumProperty(name='Group', items=[(key, label, '') for key, label in (
        ('FRONT', 'Front / Bangs'), ('SIDE', 'Side'), ('BACK', 'Back / Long'), ('UNASSIGNED', 'Unassigned'))])
    sync_mirror: BoolProperty(name='Sync Mirror Settings', default=True)
    recovery: FloatProperty(name='Shape Recovery', default=0.7, min=0, max=1)
    damping: FloatProperty(name='Damping', default=0.7, min=0, max=1)
    gravity: FloatProperty(name='Gravity', default=4, min=0, max=20, description='Gravity in meters per second squared')
    stretch: FloatProperty(name='Stretch', default=0, min=0, max=1)
    depth_index: IntProperty(default=0, min=0, max=23, options={'SKIP_SAVE'})
    depth_position: FloatProperty(name='Along Strand', default=0, min=0, max=1)
    depth_recovery: FloatProperty(name='Recovery', default=0.7, min=0, max=1)
    depth_damping: FloatProperty(name='Damping', default=0.7, min=0, max=1)
    depth_mass: FloatProperty(name='Mass', default=1, min=0.01, max=10)
    depth_gravity: FloatProperty(name='Gravity', default=4, min=0, max=20)
    last_message: StringProperty(options={'SKIP_SAVE'})


class CHARACTERDESIGNER_OT_hair_motion_initialize(Operator):
    bl_idname = 'character_designer.hair_motion_initialize'
    bl_label = 'Initialize Strand Motion'
    bl_description = 'Record stable owned strand identities and proven mirror pairs; keep bones and weights'
    bl_options = {'REGISTER', 'UNDO'}
    reconcile: BoolProperty(default=False, options={'SKIP_SAVE'})

    def execute(self, context):
        def run():
            _stop(context)
            source = _source(context)
            if source is None:
                raise ValueError('Choose the bound Hair source first.')
            old = {key: source.get(key) for key in (registry.REGISTRY_KEY, profiles.PROFILE_KEY)}
            from . import hair_motion_lifecycle
            identity_before = hair_motion_lifecycle.snapshot(source)
            try:
                data = registry.reconcile(source) if self.reconcile else registry.initialize(source)
                if self.reconcile:
                    profiles.reconcile(source, registry=data)
                else:
                    profiles.initialize(source, registry=data)
                    if old[profiles.PROFILE_KEY] is None:
                        profiles.assign_suggested_groups(source, registry=data)
                hair_motion_lifecycle.claim_bound(source, strict_registry=True)
                _load(context, source, data['strands'][0]['strand_id'], highlight=True)
            except Exception:
                for key, value in old.items():
                    if value is None:
                        source.pop(key, None)
                    else:
                        source[key] = value
                hair_motion_lifecycle.restore(source, identity_before)
                raise
        return _operate(self, context, run)


class CHARACTERDESIGNER_OT_hair_motion_navigate(Operator):
    bl_idname = 'character_designer.hair_motion_navigate'
    bl_label = 'Select Hair Strand'
    bl_description = 'Highlight one owned hair chain without changing its pose or physical settings'
    direction: IntProperty(default=1, min=-1, max=1)

    def execute(self, context):
        def run():
            _stop(context)
            source = _source(context)
            data = registry.read(source, validate=True)
            if not data:
                raise ValueError('Initialize the Hair motion strands first.')
            items = sorted(data['strands'], key=lambda item: item['order'])
            current = _state(context).strand_id
            if self.direction == 0:
                selected = getattr(context, 'active_pose_bone', None)
                current = next((item['strand_id'] for item in items if selected and selected.name in item['bones']), current)
            index = next((index for index, item in enumerate(items) if item['strand_id'] == current), 0)
            index = (index + self.direction) % len(items)
            _load(context, source, items[index]['strand_id'], highlight=True, verified_registry=data)
        return _operate(self, context, run)


class CHARACTERDESIGNER_OT_hair_motion_apply(Operator):
    bl_idname = 'character_designer.hair_motion_apply'
    bl_label = 'Apply Strand Settings'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=[(key, label, '') for key, label in (
        ('SETTINGS', 'Settings'), ('PRESET', 'Use Group Preset'),
        ('GROUP', 'Apply to Group'), ('RESTORE', 'Restore Group Defaults'))])

    def execute(self, context):
        def run():
            _stop(context)
            source, settings = _source(context), _state(context)
            data = registry.read(source, validate=True)
            strand_id = settings.strand_id
            old = source.get(profiles.PROFILE_KEY)
            try:
                if self.action in {'SETTINGS', 'PRESET', 'GROUP'}:
                    overrides = {key: getattr(settings, key) for key in profiles.RANGES} if self.action != 'PRESET' else None
                    if settings.sync_mirror:
                        # Explicitly enabling sync copies the active side's entire curve.
                        current = profiles.effective(source, strand_id, registry=data)
                        if overrides is not None:
                            scaled = json.loads(json.dumps(current['depth']))
                            for key in ('recovery', 'damping', 'gravity'):
                                for knot in scaled:
                                    knot[key] = min(profiles.DEPTH_RANGES[key][1],
                                        knot[key] * overrides[key] / current[key] if current[key] else overrides[key])
                            overrides['depth'] = scaled
                    profiles.update(source, strand_id, group=settings.group, overrides=overrides,
                                    sync_mirror=settings.sync_mirror, registry=data)
                    if self.action == 'PRESET':
                        profiles.restore_group_defaults(source, strand_id, registry=data)
                    if self.action == 'GROUP':
                        profiles.apply_to_group(source, strand_id, registry=data)
                else:
                    profiles.restore_group_defaults(source, strand_id, registry=data)
                _load(context, source, strand_id)
            except Exception:
                if old is None:
                    source.pop(profiles.PROFILE_KEY, None)
                else:
                    source[profiles.PROFILE_KEY] = old
                raise
        return _operate(self, context, run)


class CHARACTERDESIGNER_OT_hair_motion_depth(Operator):
    bl_idname = 'character_designer.hair_motion_depth'
    bl_label = 'Adjust Along Hair Strand'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=[(key, label, '') for key, label in (
        ('PREVIOUS', 'Previous'), ('NEXT', 'Next'), ('APPLY', 'Apply'), ('ADD', 'Add'), ('REMOVE', 'Remove'))])

    def execute(self, context):
        def run():
            source, settings = _source(context), _state(context)
            if self.action not in {'PREVIOUS', 'NEXT'}:
                # Stopping restores the author frame and invokes callbacks;
                # proof for a metadata edit must follow that native boundary.
                _stop(context)
            data = registry.read(source, validate=True)
            values = profiles.effective(source, settings.strand_id, registry=data)
            knots = values['depth']
            index = min(settings.depth_index, len(knots) - 1)
            if self.action in {'PREVIOUS', 'NEXT'}:
                settings.depth_index = (index + (1 if self.action == 'NEXT' else -1)) % len(knots)
                _load_depth(context, values)
                return
            old, old_index = source.get(profiles.PROFILE_KEY), settings.depth_index
            try:
                if self.action == 'REMOVE':
                    if index in {0, len(knots) - 1}:
                        raise ValueError('Keep the root and tip controls.')
                    knots.pop(index)
                elif self.action == 'ADD':
                    if index == len(knots) - 1:
                        index -= 1
                    position = (knots[index]['position'] + knots[index + 1]['position']) / 2
                    knot = {'position': position, **profiles.depth_sample(values, position)}
                    knots.insert(index + 1, knot)
                    settings.depth_index = index + 1
                else:
                    knot = {key: getattr(settings, 'depth_' + key) for key in ('position', 'recovery', 'damping', 'mass', 'gravity')}
                    if index in {0, len(knots) - 1}:
                        knot['position'] = knots[index]['position']
                    knots[index] = knot
                profiles.update(source, settings.strand_id, overrides={'depth': knots}, registry=data)
                _load(context, source, settings.strand_id)
            except Exception:
                source[profiles.PROFILE_KEY] = old
                settings.depth_index = old_index
                raise
        return _operate(self, context, run)


class CHARACTERDESIGNER_OT_hair_motion_preview(Operator):
    bl_idname = 'character_designer.hair_motion_preview'
    bl_label = 'Preview Hair Motion'
    scope: EnumProperty(items=[('PAIR', 'Current Pair', ''), ('ALL', 'All Strands', ''), ('STOP', 'Stop', '')])

    def execute(self, context):
        def run():
            if self.scope == 'STOP':
                _backend().stop_preview(context)
                return
            source, settings = _source(context), _state(context)
            data = registry.read(source, validate=True)
            if not data:
                raise ValueError('Initialize motion settings before previewing Hair.')
            item = next((item for item in data['strands'] if item['strand_id'] == settings.strand_id), None)
            if self.scope == 'PAIR' and item is None:
                raise ValueError('Select a strand from this Hair source before previewing.')
            _stop(context)
            ids = [item['strand_id'] for item in data['strands']] if self.scope == 'ALL' else [settings.strand_id]
            if self.scope == 'PAIR':
                if item['mirror_id'] and item['mirror_id'] != item['strand_id']:
                    ids.append(item['mirror_id'])
            _backend().start_preview(context, source, data, profiles.effective_all(source, registry=data), ids)
        return _operate(self, context, run)


class CHARACTERDESIGNER_OT_hair_motion_pair(Operator):
    bl_idname = 'character_designer.hair_motion_pair'
    bl_label = 'Pair Hair Strands'
    bl_description = 'Explicitly declare the current left and right strands; preserve each side until mirror sync is enabled'
    bl_options = {'REGISTER', 'UNDO'}
    side: EnumProperty(items=[('LEFT', 'Mark Left', ''), ('RIGHT', 'Pair Right', '')])

    def execute(self, context):
        def run():
            source, settings = _source(context), _state(context)
            data = registry.read(source, validate=True)
            if not data or settings.strand_id not in {item['strand_id'] for item in data['strands']}:
                raise ValueError('Select a strand from this Hair source first.')
            if self.side == 'LEFT':
                settings.pair_left_id = settings.strand_id
                return
            _stop(context)
            before = {key: source.get(key) for key in (registry.REGISTRY_KEY, profiles.PROFILE_KEY)}
            try:
                data = registry.manual_pair(source, settings.pair_left_id, settings.strand_id)
                profiles.reconcile(source, registry=data)
                _load(context, source, settings.strand_id)
            except Exception:
                for key, value in before.items():
                    if value is None:
                        source.pop(key, None)
                    else:
                        source[key] = value
                raise
        return _operate(self, context, run)


class CHARACTERDESIGNER_PT_hair_motion(Panel):
    bl_label = 'Strand Motion'
    bl_idname = 'CHARACTERDESIGNER_PT_hair_motion'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = SIDEBAR_CATEGORY

    @classmethod
    def poll(cls, context):
        return rig_page_active(context, 'HAIR')

    def draw(self, context):
        layout, settings, source = self.layout, _state(context), _source(context)
        if not settings or source is None:
            layout.label(text='Choose the bound Hair source.')
            return
        try:
            # Draw only saved metadata. Native proof runs in every action, never
            # an expensive full-mesh mirror search on each sidebar redraw.
            data = registry.read(source, validate=False)
            profile = profiles.read(source, registry=data) if data else None
        except (ValueError, RuntimeError) as exc:
            layout.label(text=str(exc), icon='ERROR')
            op = layout.operator('character_designer.hair_motion_initialize', text='Reconcile Strands')
            op.reconcile = True
            return
        if data is None or profile is None:
            layout.operator('character_designer.hair_motion_initialize')
            return
        # Saved metadata can still parse after an intentional Rest/segment edit.
        # Keep its explicit reconciliation action available without doing the
        # full native ownership/structure proof during every sidebar redraw.
        op = layout.operator('character_designer.hair_motion_initialize', text='Reconcile Strands')
        op.reconcile = True
        items = sorted(data['strands'], key=lambda item: item['order'])
        item = next((item for item in items if item['strand_id'] == settings.strand_id), None)
        row = layout.row(align=True)
        row.operator('character_designer.hair_motion_navigate', text='Previous').direction = -1
        row.operator('character_designer.hair_motion_navigate', text='Next').direction = 1
        row.operator('character_designer.hair_motion_navigate', text='Selected').direction = 0
        if item is None:
            layout.label(text='Select a strand to edit its settings.')
            return
        layout.label(text=f'Strand {items.index(item) + 1} / {len(items)} · {item["side"]}')
        layout.label(text=item['bones'][0])
        layout.prop(settings, 'group')
        layout.operator('character_designer.hair_motion_apply', text='Use Group Preset').action = 'PRESET'
        row = layout.row()
        row.enabled = bool(item['mirror_id']) and item['side'] != 'C'
        row.prop(settings, 'sync_mirror')
        if not item['mirror_id'] or item['side'] == 'C':
            layout.label(text='Center strand' if item['side'] == 'C' else 'No proven mirror pair', icon='INFO')
        if item['side'] == 'U':
            row = layout.row(align=True)
            row.operator('character_designer.hair_motion_pair', text='Mark Left').side = 'LEFT'
            row.operator('character_designer.hair_motion_pair', text='Pair Right').side = 'RIGHT'
        for key in profiles.RANGES:
            layout.prop(settings, key)
        layout.operator('character_designer.hair_motion_apply').action = 'SETTINGS'
        row = layout.row(align=True)
        row.operator('character_designer.hair_motion_apply', text='Apply to Group').action = 'GROUP'
        row.operator('character_designer.hair_motion_apply', text='Restore Defaults').action = 'RESTORE'
        layout.prop(settings, 'show_depth', icon='TRIA_DOWN' if settings.show_depth else 'TRIA_RIGHT', emboss=False)
        if settings.show_depth:
            box = layout.box()
            values = profiles.effective(source, item['strand_id'], registry=data, record=profile)
            box.label(text=f'Along Strand · {settings.depth_index + 1} / {len(values["depth"])}')
            row = box.row(align=True)
            row.operator('character_designer.hair_motion_depth', text='Previous').action = 'PREVIOUS'
            row.operator('character_designer.hair_motion_depth', text='Next').action = 'NEXT'
            for key in ('position', 'recovery', 'damping', 'mass', 'gravity'):
                box.prop(settings, 'depth_' + key)
            row = box.row(align=True)
            row.operator('character_designer.hair_motion_depth', text='Apply').action = 'APPLY'
            row.operator('character_designer.hair_motion_depth', text='Add').action = 'ADD'
            row.operator('character_designer.hair_motion_depth', text='Remove').action = 'REMOVE'
        backend = _backend()
        status = backend.status(context)
        if status.get('active'):
            layout.operator('character_designer.hair_motion_preview', text='Stop Preview').scope = 'STOP'
        else:
            available = backend.available(context)
            row = layout.row(align=True)
            row.enabled = bool(available.get('available'))
            row.operator('character_designer.hair_motion_preview', text='Preview Pair').scope = 'PAIR'
            row.operator('character_designer.hair_motion_preview', text='Preview All').scope = 'ALL'
            if not available.get('available'):
                layout.label(text=available.get('reason', 'Enable Wiggle Bones 1.1.2 first.'), icon='INFO')
        layout.label(text='Preview uses timeline playback.')
        if settings.last_message:
            layout.label(text=settings.last_message, icon='ERROR')


HAIR_MOTION_CLASSES = (CharacterDesignerHairMotionState, CHARACTERDESIGNER_OT_hair_motion_initialize,
                       CHARACTERDESIGNER_OT_hair_motion_navigate, CHARACTERDESIGNER_OT_hair_motion_apply,
                       CHARACTERDESIGNER_OT_hair_motion_depth, CHARACTERDESIGNER_OT_hair_motion_preview,
                       CHARACTERDESIGNER_OT_hair_motion_pair,
                       CHARACTERDESIGNER_PT_hair_motion)
