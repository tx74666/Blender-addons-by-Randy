"""Five active-side indicators with compact, saved L/R diagnostics."""
import json
import textwrap
from functools import lru_cache
import bpy
from bpy.props import BoolProperty, CollectionProperty, PointerProperty, StringProperty, EnumProperty
from bpy.types import Operator, PropertyGroup
from . import finger_bank as bank, finger_detect as detect, finger_definition as definition
from .finger_definition_ui import CharacterDesignerFingerDefinition


@lru_cache(maxsize=32)
def _parsed(record):
    # These read-only panel summaries do not validate or mutate definitions.
    return json.loads(record)


class CharacterDesignerFingerSlot(PropertyGroup):
    guide: PointerProperty(type=CharacterDesignerFingerDefinition)
    error: StringProperty()
    bones: StringProperty(options={'HIDDEN'})


class CharacterDesignerFingerBank(PropertyGroup):
    slots: CollectionProperty(type=CharacterDesignerFingerSlot)
    active: StringProperty()
    visible_digits: EnumProperty(items=[(d, detect.LABELS[d], '', 1 << i) for i, d in enumerate(detect.DIGITS)],
                                 options={'ENUM_FLAG', 'HIDDEN'}, default=set())
    selection_initialized: BoolProperty(default=False, options={'HIDDEN'})
    selection_anchor: StringProperty(options={'HIDDEN'})
    survey: StringProperty()
    status: StringProperty(options={'SKIP_SAVE'})
    bone_status: StringProperty(options={'SKIP_SAVE'})
    needs_recheck: StringProperty(options={'SKIP_SAVE'})


class CHARACTERDESIGNER_OT_finger_setup(Operator):
    bl_idname = 'character_designer.finger_setup'
    bl_label = 'Finger Basic Setup'
    bl_options = {'REGISTER'}
    action: EnumProperty(items=[(s, s.title(), '') for s in ('CAPTURE', 'START', 'END', 'SELECT', 'SIDE', 'RECHECK', 'CLEAR', 'TOGGLE')])
    digit: EnumProperty(items=[(d, detect.LABELS[d], '') for d in detect.DIGITS])
    selection_mode: EnumProperty(items=[(m, m.title(), '') for m in ('CLICK', 'SINGLE', 'TOGGLE', 'RANGE')],
                                default='CLICK', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'SELECT':
            return detect.LABELS[properties.digit]+': click an unselected button for one; click a selected button to hide it (none selected is allowed); Shift-click adds/removes; Ctrl-Shift-click adds the inclusive range from the last selection anchor. Viewing only; edits use the active side'
        if properties.action == 'TOGGLE':
            from . import finger_definition_ui
            obj = bank.active_object(context)
            if obj:
                return f'Show / hide all finger overlays on the preview side ({len(bank.selected_digits(obj.character_designer_finger_bank))} fingers selected); no mesh or bone edits'
            data, _ = finger_definition_ui.cached_frame(context)
            return ('Show / hide the internal straight axis'+(f"; length {data['length']:.4g} Blender units" if data else ''))
        if properties.action == 'SIDE':
            obj = bank.active_object(context)
            side = bank.display_side(obj.character_designer_finger_bank) if obj else 'L'
            return f'Active side: {side}. Switch left / right saved guides and setup'
        if properties.action == 'RECHECK':
            return 'Recheck the active finger reference only; retain the opposite side and all other saved references'
        if properties.action == 'CLEAR':
            return 'Clear this finger side\'s reference, errors, bone binding and loop marks; keep the opposite side, other fingers and the mesh'
        return 'Capture the selected finger side; retain the opposite reference and mesh'

    def invoke(self, context, event):
        if self.action == 'SELECT':
            self.selection_mode = 'RANGE' if event.ctrl and event.shift else 'TOGGLE' if event.shift else 'CLICK'
        return self.execute(context)

    def execute(self, context):
        from . import finger_definition_ui as ui
        try:
            if self.action not in {'CAPTURE', 'START', 'END'} and not bank.active_object(context):
                raise ValueError('Capture a finger on this mesh first.')
            if self.action in {'CAPTURE', 'START', 'END'}:
                bank.capture(context, self.action)
            elif self.action == 'SELECT': bank.select(context, self.digit, mode=self.selection_mode)
            elif self.action == 'SIDE':
                bank.switch_side(context)
            elif self.action == 'RECHECK': bank.recheck(context)
            elif self.action == 'TOGGLE':
                master = context.scene.character_designer_finger_definition
                master.overlays_enabled = not master.overlays_enabled
                return {'FINISHED'}
            elif self.action == 'CLEAR':
                bank.clear(context)
                return {'FINISHED'}
            obj = bank.active_object(context)
            if obj:
                obj.character_designer_finger_bank.status = ''
                obj.character_designer_finger_bank.bone_status = ''
            from . import finger_flex, finger_bone_tools, finger_loop_marks_ui
            finger_loop_marks_ui.refresh(context)
            finger_bone_tools.refresh_selection(context)
            finger_flex._visible = False
            if self.action in {'CAPTURE', 'SELECT', 'SIDE', 'CLEAR'}:
                finger_flex.state(context).status = ''
            if self.action in {'CAPTURE', 'SELECT'} or ui._visible:
                ui.show(invalidate=self.action not in {'SELECT', 'SIDE'})
            else: ui.redraw()
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, IndexError, ReferenceError) as exc:
            from . import finger_bone_tools
            finger_bone_tools.invalidate(context)
            message = 'Update failed; previous result retained: '+str(exc)
            self.report({'WARNING'}, message)
            obj = bank.active_object(context)
            if obj: obj.character_designer_finger_bank.status = message
            else: definition.state(context).status = message
            ui.redraw()
            return {'CANCELLED'}


def _pair_notice(message):
    # Pair diagnostics are informational; drawing never reads current geometry.
    return 'L/R differ'


def _pair_only(message, warning=''):
    """Recognize saved pair-only errors without changing their metadata."""
    message = message.removeprefix('Update failed; previous result retained: ')
    return bool(message and (message == warning or message in {
        bank.PAIR_SYNC_WARNING, 'Opposite finger / closed tip not found.',
        'Left/right finger surfaces differ. Match the mesh shape on both sides before paired bone actions.'}))


def draw_header(box, context):
    from . import finger_targets
    obj = bank.active_object(context)
    b = obj.character_designer_finger_bank if obj else None
    report = _parsed(b.survey) if b and b.survey else {'warnings': {}, 'candidates': {}}
    selected = bank.selected_digits(b) if b else ()
    side = bank.display_side(b) if b else 'L'
    row = box.row(align=True)
    for digit, label in zip(detect.DIGITS, ('Th', 'I', 'M', 'R', 'P')):
        slot = b.slots.get(f'{digit}.{side}') if b else None
        partial = bank.configured(slot)
        reference_error = finger_targets.reference_error(obj, slot.name) if partial else ''
        ready = bool(slot and slot.guide.record and slot.guide.confirmed and not slot.guide.pending
                     and _parsed(slot.guide.record).get('internal'))
        button = row.row(align=True)
        button.enabled, button.alert = b is not None, bool(reference_error)
        op = button.operator('character_designer.finger_setup', text=label,
                             icon='ERROR' if reference_error else 'CHECKMARK' if ready else 'QUESTION' if partial else 'RADIOBUT_OFF',
                             depress=digit in selected)
        op.action, op.digit = 'SELECT', digit
    if b and b.active:
        digit = b.active.split('.')[0]
        row = box.row(align=True)
        row.label(text=detect.LABELS[digit] if len(selected) == 1 else f'Active: {detect.LABELS[digit]} · {len(selected)} selected')
        row.operator('character_designer.finger_setup', text='', icon='ARROW_LEFTRIGHT',
                     depress=bank.display_side(b) == 'R').action = 'SIDE'
        row.operator('character_designer.finger_setup', text='', icon='FILE_REFRESH').action = 'RECHECK'
        mirror = row.row(align=True)
        mirror.enabled = context.mode == 'EDIT_MESH' and context.edit_object == obj
        mirror.operator('character_designer.mirror_selected_region', text='', icon='MOD_MIRROR').sync_fingers = True
        active = b.slots.get(b.active)
        pair_warning = report['warnings'].get(digit, '')
        if bank.configured(active) and pair_warning:
            row.label(text=_pair_notice(pair_warning), icon='INFO')
        # An old opposite-side error or global recheck banner cannot turn the
        # working side red. Only this side's explicit error is shown here.
        errors = [b.status] if b.status and not _pair_only(b.status, pair_warning) else []
        reference_error = finger_targets.reference_error(obj, active.name) if bank.configured(active) else ''
        if reference_error: errors.append(f'{side} reference: {reference_error}')
        for error in dict.fromkeys(errors):
            warning = box.column()
            warning.alert = True
            for line in textwrap.wrap(error, 33): warning.label(text=line)


CLASSES = (CharacterDesignerFingerSlot, CharacterDesignerFingerBank, CHARACTERDESIGNER_OT_finger_setup)


def register_runtime():
    bpy.types.Object.character_designer_finger_bank = PointerProperty(type=CharacterDesignerFingerBank)
    bpy.types.Scene.character_designer_finger_setup = PointerProperty(type=bpy.types.Object)


def unregister_runtime():
    _parsed.cache_clear()
    for cls, key in ((bpy.types.Object, 'character_designer_finger_bank'), (bpy.types.Scene, 'character_designer_finger_setup')):
        if hasattr(cls, key): delattr(cls, key)
