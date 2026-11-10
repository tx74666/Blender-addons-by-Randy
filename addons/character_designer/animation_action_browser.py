"""A display-only daily Action list, with Blender's complete selector retained.

Classification is explicit. This module never infers a version from a name,
rewrites an existing ownership record, or scans animation channels/keys.
"""

import sys

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator


PREFIX = 'character_designer_action_browser_v1_'
ROLE_KEY, LABEL_KEY = PREFIX + 'role', PREFIX + 'label'
CLIP_KEY, OWNER_KEY, DATA_KEY = PREFIX + 'clip', PREFIX + 'owner', PREFIX + 'owner_data'
SHOW_ALL = 'character_designer_action_browser_all'
ROLES = frozenset({'WORK', 'ARCHIVED', 'INPUT', 'CHECKPOINT'})
_KEYS = (ROLE_KEY, LABEL_KEY, CLIP_KEY, OWNER_KEY, DATA_KEY)
_hook = None
_property_owned = False
_property_identity = None
_recent = {}


def _uid(block):
    return str(block.session_uid)


def _editable(block):
    return (block is not None and block.library is None
            and getattr(block, 'override_library', None) is None
            and getattr(block, 'is_editable', True))


def classify_action(action, rig, *, role, label='', clip_id=''):
    """Set only our display metadata on one explicitly identified local Action.

    Callers authenticate the artist's classification before calling this API.
    Owner names are display hints only, never editing/history authority. No ID
    references are added. Linked Sources are never written.
    """
    if role not in ROLES or not _editable(action) or not _editable(rig) or rig.type != 'ARMATURE':
        raise ValueError('Display classification needs a local editable Action and rig, and a known role.')
    if not isinstance(label, str) or not isinstance(clip_id, str):
        raise ValueError('Display label and clip identity must be text.')
    if role == 'WORK' and (not label.strip() or not clip_id.strip()):
        raise ValueError('A working motion needs its explicit clip identity and display name.')
    previous_owner = action.get(OWNER_KEY)
    if previous_owner is not None and not _owned_hint(action, rig):
        raise ValueError('This display classification belongs to another rig.')
    before = {key: action[key] for key in _KEYS if key in action}
    try:
        for key, value in ((ROLE_KEY, role), (LABEL_KEY, label.strip()),
                           (CLIP_KEY, clip_id.strip()), (OWNER_KEY, rig.name),
                           (DATA_KEY, rig.data.name)):
            action[key] = value
    except Exception:
        for key in _KEYS:
            if key in before:
                action[key] = before[key]
            elif key in action:
                del action[key]
        raise


def _owned_hint(action, rig):
    return action.get(OWNER_KEY) == rig.name and action.get(DATA_KEY) == rig.data.name


def _foreign_hint(action, rig):
    return (any(key in action for key in (OWNER_KEY, DATA_KEY))
            and not _owned_hint(action, rig))


def display_role(action, rig):
    if action.library is not None:
        return 'SOURCE'
    if _owned_hint(action, rig) and action.get(ROLE_KEY) in ROLES:
        return action.get(ROLE_KEY)
    return 'UNKNOWN'


def display_label(action, rig):
    label = action.get(LABEL_KEY) if _owned_hint(action, rig) else None
    if isinstance(label, str) and label.strip():
        return label
    clip_name = action.get('unity_clip_name')
    return clip_name.strip() if isinstance(clip_name, str) and clip_name.strip() else action.name


def _slots(action):
    """Suitable Object slots only; no slot-name or curve-path ownership guess."""
    if not action.is_action_layered:
        return [None] if getattr(action, 'id_root', 'OBJECT') in {'OBJECT', '', None} else []
    slots = list(action.slots)
    if not slots:
        return [None]  # A new empty Action may acquire its first slot natively.
    return [slot for slot in slots if slot.target_id_type in {'OBJECT', 'UNSPECIFIED'}]


def daily_rows(target):
    """Enumerate metadata/slots when the menu opens, never from header drawing."""
    ad = target.animation_data
    current = ad.action if ad else None
    current_handle = ad.action_slot.handle if ad and ad.action_slot else 0
    candidates = []
    for action in bpy.data.actions:
        active = action is current
        if not active and (not _editable(action) or display_role(action, target) not in {'WORK', 'UNKNOWN'}):
            continue
        candidates.append(action)
    groups = {}
    for action in candidates:
        clip = action.get(CLIP_KEY)
        if display_role(action, target) == 'WORK' and isinstance(clip, str) and clip:
            groups.setdefault(clip, []).append(action)
    omitted = set()
    for clip, group in groups.items():
        key = (target.name, target.data.name, clip)
        chosen = current if any(action is current for action in group) else next(
            (action for action in group if _uid(action) == _recent.get(key)), None)
        if chosen is not None:
            omitted.update(_uid(action) for action in group if action is not chosen)
    rows = []
    for action in candidates:
        if _uid(action) in omitted:
            continue
        active = action is current
        slots = _slots(action)
        if active and not any((slot.handle if slot else 0) == current_handle for slot in slots):
            slots.insert(0, ad.action_slot)
        for slot in slots:
            handle = slot.handle if slot else 0
            label = display_label(action, target)
            if len(slots) > 1:
                label += ' · ' + (slot.name_display if slot else 'No Slot')
            rows.append({'action': action, 'slot': handle, 'label': label,
                         'current': active and handle == current_handle,
                         'editable': _editable(action) and not _foreign_hint(action, target)})
    # Equal display names alone do not merge motions; uncertain groups stay open.
    return sorted(rows, key=lambda row: (not row['current'], row['label'].casefold(), _uid(row['action']), row['slot']))


def _animation():
    return sys.modules.get(__package__ + '.animation')


def _eligible(context, target):
    animation = _animation()
    if (animation is None or target is None or target.type != 'ARMATURE'
            or not _editable(target) or animation._target(context) is not target):
        return False
    saved = getattr(context.scene, 'character_designer_animation_worklist', None)
    # Managed selection must continue through its own admission/owner contract.
    if saved is not None and (saved.workspace_path or saved.items):
        return False
    return (context.scene.objects.get(target.name) is target
            and context.view_layer.objects.get(target.name) is target)


def _idle(context, target):
    """Display/native-selector admission without claiming a managed switch."""
    if not _eligible(context, target) or context.mode not in {'OBJECT', 'POSE'}:
        return False
    ad = target.animation_data
    if ad and ad.use_tweak_mode:
        return False
    if any(key in target for key in ('character_designer_body_original_mode_v1',
                                     'character_designer_base_pose_test_v1',
                                     'character_designer_natural_pose_test_v1',
                                     'character_designer_rigging_session_v1',
                                     'character_designer_composition_session_v1')):
        return False
    action = ad.action if ad else None
    if action is not None and any(key in action for key in ('character_designer_base_pose_test_session_v1',
                                                            'character_designer_base_pose_test_refs_v1')):
        return False
    if getattr(getattr(context, 'screen', None), 'is_animation_playing', False):
        return False
    animation = _animation()
    if not animation._link_idle():
        return False
    unity = sys.modules.get(__package__ + '.unity_animation')
    if unity is None or unity.active_preview(target):
        return False
    # Other owned conversions/Composition must not be interrupted by this list.
    for name in ('controller_animation', 'animation_composition', 'unity_animation_rigging'):
        module = sys.modules.get(__package__ + '.' + name)
        if module is not None and getattr(module, '_ACTIVE_JOB', None) is not None:
            return False
    composition = sys.modules.get(__package__ + '.animation_composition')
    return not (composition and composition.active(target))


def can_select(context, target):
    """Daily-list switching retains the existing managed-owner restriction."""
    return _idle(context, target) and not any(key in target for key in (
        'character_designer_worklist_main_session_v1', 'character_designer_worklist_main_restored_v1'))


def activate(context, *, target_uid, action_uid, slot_handle):
    """Choose fresh Action/slot and retain local work; roll back flags/binding.

    Fake User retains the work left behind when its active binding disappears.
    Only local editable WORK/UNKNOWN/ARCHIVED motions are retained; Source and
    technical input/checkpoint Actions keep their existing flags.
    """
    target = next((obj for obj in bpy.data.objects if _uid(obj) == target_uid), None)
    if not can_select(context, target):
        raise ValueError('Return to idle, ordinary animation editing before choosing a working motion.')
    choices = [row for row in daily_rows(target) if _uid(row['action']) == action_uid
               and row['slot'] == slot_handle and row['editable']]
    if len(choices) != 1:
        raise ValueError('This motion or slot changed; reopen the working motion list.')
    action = choices[0]['action']
    _check_slot_consumers(target, action, slot_handle)
    slot = next((slot for slot in action.slots if slot.handle == slot_handle), None) if slot_handle else None
    old_ad = target.animation_data
    old_action = old_ad.action if old_ad else None
    old_slot = old_ad.action_slot if old_ad else None
    pins = []
    for selected in (old_action, action):
        if (selected is not None and _editable(selected) and not _foreign_hint(selected, target)
                and display_role(selected, target) in {'WORK', 'UNKNOWN', 'ARCHIVED'}
                and not any(selected is item for item, _flag in pins)):
            pins.append((selected, selected.use_fake_user))
    ad = old_ad if old_ad else target.animation_data_create()
    try:
        ad.action = action
        ad.action_slot = slot
        if ad.action is not action or (ad.action_slot.handle if ad.action_slot else 0) != slot_handle:
            raise ValueError('Blender did not retain the chosen Action and slot.')
        for selected, before in pins:
            if not before:
                selected.use_fake_user = True
            if not selected.use_fake_user:
                raise ValueError('Blender did not retain the working motion.')
    except Exception as error:
        recovery_errors = []
        for selected, before in reversed(pins):
            try:
                if selected.use_fake_user != before:
                    selected.use_fake_user = before
            except Exception as recovery:
                recovery_errors.append(str(recovery))
        try:
            if old_ad is None:
                target.animation_data_clear()
            else:
                ad.action = old_action
                ad.action_slot = old_slot
        except Exception as recovery:
            recovery_errors.append(str(recovery))
        if recovery_errors:
            raise RuntimeError('Motion selection failed and restoring its retention flags or Action/slot also failed: '
                               + '; '.join(recovery_errors)) from error
        raise
    for selected in (old_action, action):
        if selected is not None and display_role(selected, target) == 'WORK':
            clip = selected.get(CLIP_KEY)
            if isinstance(clip, str) and clip:
                _recent[(target.name, target.data.name, clip)] = _uid(selected)
    return action


def _check_slot_consumers(target, action, slot_handle):
    """Header-only check at selection time; other rigs' live inputs stay theirs."""
    def consumes(holder):
        if getattr(holder, 'action', None) is not action:
            return False
        slot = getattr(holder, 'action_slot', None)
        handle = slot.handle if slot is not None else getattr(holder, 'action_slot_handle', None)
        if handle is None:
            # Legacy has no slots; an unrecognised layered header cannot prove
            # that this particular slot is free of another armature consumer.
            return not action.is_action_layered or bool(action.slots)
        return handle == slot_handle

    def strips_consume(strips):
        for strip in strips:
            if consumes(strip) or (getattr(strip, 'type', None) == 'META'
                                   and strips_consume(strip.strips)):
                return True
        return False

    for other in bpy.data.objects:
        if other is target or getattr(other, 'type', None) != 'ARMATURE':
            continue
        ad = other.animation_data
        if ad is not None and (consumes(ad) or any(strips_consume(track.strips) for track in ad.nla_tracks)):
            raise ValueError('This Action slot is already used by another armature; use its own working motion.')


class CHARACTERDESIGNER_OT_choose_working_action(Operator):
    bl_idname = 'character_designer.choose_working_action'
    bl_label = 'Choose Working Motion'
    bl_options = {'REGISTER', 'UNDO'}
    target_uid: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    action_uid: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    slot_handle: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            activate(context, target_uid=self.target_uid, action_uid=self.action_uid, slot_handle=self.slot_handle)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_MT_working_actions(Menu):
    bl_label = 'Working Motions'

    def draw(self, context):
        target = _animation()._target(context) if _animation() else None
        if not _idle(context, target):
            self.layout.label(text='Finish the current animation operation first.', icon='INFO')
            return
        rows = daily_rows(target)
        switchable = can_select(context, target)
        if not rows:
            self.layout.label(text='No working motions. Use All to choose or create an Action.')
        for row in rows:
            layout = self.layout.row()
            layout.enabled = row['editable'] and switchable
            op = layout.operator(CHARACTERDESIGNER_OT_choose_working_action.bl_idname, text=row['label'],
                                 icon='CHECKMARK' if row['current'] else 'ACTION')
            op.target_uid, op.action_uid, op.slot_handle = _uid(target), _uid(row['action']), row['slot']


def _draw_native(layout, target):
    layout.template_action(target, new='action.new', unlink='action.unlink', text='Action')
    ad = target.animation_data
    if ad and ad.action:
        layout.context_pointer_set('animated_id', target)
        layout.template_search(ad, 'action_slot', ad, 'action_suitable_slots',
                               new='anim.slot_new_for_id', unlink='anim.slot_unassign_from_id')


def draw_selector(layout, context, target, *, render_all_native=True):
    """Return False for native delegation; do not draw the native row twice.

    The header uses render_all_native=False and calls its captured native
    method when False is returned. Sidebar callers may use the default.
    """
    if not _eligible(context, target) or not hasattr(context.window_manager, SHOW_ALL):
        return False
    row = layout.row(align=True)
    enabled = _idle(context, target)
    all_actions = getattr(context.window_manager, SHOW_ALL)
    if not all_actions:
        choose = row.row(align=True)
        choose.enabled = enabled
        action = target.animation_data.action if target.animation_data else None
        choose.menu(CHARACTERDESIGNER_MT_working_actions.__name__,
                    text=display_label(action, target) if action else 'Choose Action', icon='ACTION')
    row.prop(context.window_manager, SHOW_ALL, text='All', toggle=True)
    if all_actions:
        if not render_all_native:
            return False
        native = row.row(align=True)
        native.enabled = enabled
        _draw_native(native, target)
    return True


CLASSES = (CHARACTERDESIGNER_OT_choose_working_action, CHARACTERDESIGNER_MT_working_actions)
classes = CLASSES


def _install_header():
    global _hook
    if _hook is not None and _hook['enabled']:
        return
    from bl_ui.space_dopesheet import DOPESHEET_HT_editor_buttons as owner
    previous = owner.__dict__.get('_draw_action_selector')
    if not isinstance(previous, classmethod):
        raise RuntimeError('The native Action selector has an unsupported extension; it was kept.')
    state = {'enabled': True, 'owner': owner, 'previous': previous}

    def wrapper(cls, context, layout):
        if state['enabled'] and getattr(context.space_data, 'mode', None) == 'ACTION':
            target = context.object
            if _eligible(context, target):
                if draw_selector(layout, context, target, render_all_native=False):
                    return
                native = layout.row(align=True)
                native.enabled = _idle(context, target)
                return previous.__get__(None, cls)(context, native)
        return previous.__get__(None, cls)(context, layout)

    state['descriptor'] = classmethod(wrapper)
    owner._draw_action_selector = state['descriptor']
    _hook = state


def _remove_header():
    global _hook
    if _hook is None:
        return
    state = _hook
    state['enabled'] = False
    # A later add-on may wrap us. Keep its top-level descriptor and make our
    # retained inner wrapper transparently delegate after this module disables.
    if state['owner'].__dict__.get('_draw_action_selector') is state['descriptor']:
        state['owner']._draw_action_selector = state['previous']
    _hook = None


def register():
    """Transactional, idempotent registration; no handlers, timers or data edits."""
    global _property_owned, _property_identity
    added = []
    added_property = False
    try:
        for cls in CLASSES:
            existing = getattr(bpy.types, cls.__name__, None)
            if existing is not None and existing is not cls:
                raise RuntimeError('A foreign working-motion UI class was retained: ' + cls.__name__)
            if not getattr(cls, 'is_registered', False):
                bpy.utils.register_class(cls)
                added.append(cls)
        if hasattr(bpy.types.WindowManager, SHOW_ALL):
            if not _property_owned or not _same_property(_view_property(), _property_identity):
                raise RuntimeError('A foreign working-motion view property was retained.')
        else:
            setattr(bpy.types.WindowManager, SHOW_ALL, BoolProperty(name='All', default=False,
                    description='Show Blender\'s complete native Action selector', options={'SKIP_SAVE'}))
            _property_identity = _view_property()
            if _property_identity is None:
                raise RuntimeError('The working-motion view property did not register in RNA.')
            added_property = _property_owned = True
        _install_header()
    except Exception:
        if added_property:
            if _same_property(_view_property(), _property_identity):
                delattr(bpy.types.WindowManager, SHOW_ALL)
            _property_owned = False
            _property_identity = None
        for cls in reversed(added):
            bpy.utils.unregister_class(cls)
        raise


def unregister():
    global _property_owned, _property_identity
    _remove_header()
    if _property_owned and _same_property(_view_property(), _property_identity):
        delattr(bpy.types.WindowManager, SHOW_ALL)
    _property_owned = False
    _property_identity = None
    _recent.clear()
    for cls in reversed(CLASSES):
        if getattr(cls, 'is_registered', False):
            bpy.utils.unregister_class(cls)


def _view_property():
    return bpy.types.WindowManager.bl_rna.properties.get(SHOW_ALL)


def _same_property(current, owned):
    if current is None or owned is None:
        return False
    if current is owned:
        return True
    try:
        return current.as_pointer() == owned.as_pointer()
    except (AttributeError, ReferenceError):
        return False
