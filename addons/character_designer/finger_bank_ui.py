"""One operation row and five pair indicators, not ten setup forms."""
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
            return detect.LABELS[properties.digit]+' pair: click an unselected button for one; click a selected button to hide it (none selected is allowed); Shift-click adds/removes; Ctrl-Shift-click adds the inclusive range from the last selection anchor. Viewing only; edits still use the active finger'
        if properties.action == 'TOGGLE':
            from . import finger_definition_ui
            obj = bank.active_object(context)
            if obj:
                return f'Show / hide the highlighted finger pairs ({len(bank.selected_digits(obj.character_designer_finger_bank))} selected); no mesh or bone edits'
            data, _ = finger_definition_ui.cached_frame(context)
            return ('Show / hide the internal straight axis'+(f"; length {data['length']:.4g} Blender units" if data else ''))
        return 'Detect the selected finger and its opposite; reference settings only, no mesh repair'

    def invoke(self, context, event):
        if self.action == 'SELECT':
            self.selection_mode = 'RANGE' if event.ctrl and event.shift else 'TOGGLE' if event.shift else 'CLICK'
        return self.execute(context)

    def execute(self, context):
        from . import finger_definition_ui as ui
        try:
            if self.action not in {'CAPTURE', 'START', 'END'} and not bank.active_object(context):
                raise ValueError('Capture a finger on this mesh first.')
            if self.action in {'CAPTURE', 'START', 'END'}: bank.capture(context, self.action)
            elif self.action == 'SELECT': bank.select(context, self.digit, mode=self.selection_mode)
            elif self.action == 'SIDE':
                b = bank.active_object(context).character_designer_finger_bank
                bank.select(context, b.active.split('.')[0], 'R' if b.active[-1] == 'L' else 'L')
            elif self.action == 'RECHECK': bank.recheck(context)
            elif self.action == 'TOGGLE':
                if ui._visible: ui.hide()
                else: ui.show(invalidate=False)
                return {'FINISHED'}
            elif self.action == 'CLEAR':
                b = bank.active_object(context).character_designer_finger_bank
                for slot in b.slots:
                    if slot.name.split('.')[0] == b.active.split('.')[0]:
                        definition.clear(bank.scoped(context, slot.guide))
                        slot.error = ''
            obj = bank.active_object(context)
            if obj: obj.character_designer_finger_bank.status = ''
            from . import finger_layout, finger_layout_ui, finger_flex, finger_workflow_ui
            finger_workflow_ui.refresh(context)
            finger_layout_ui.hide_preview()
            finger_flex._visible = False
            if self.action in {'CAPTURE', 'SELECT', 'SIDE', 'CLEAR'}:
                finger_layout.state(context).status = ''
                finger_flex.state(context).status = ''
            if self.action in {'CAPTURE', 'SELECT'} or ui._visible:
                ui.show(invalidate=self.action not in {'SELECT', 'SIDE'})
            else: ui.redraw()
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, IndexError, ReferenceError) as exc:
            from . import finger_workflow_ui
            finger_workflow_ui.refresh(context)
            message = 'Update failed; previous result retained: '+str(exc)
            self.report({'WARNING'}, message)
            obj = bank.active_object(context)
            if obj: obj.character_designer_finger_bank.status = message
            else: definition.state(context).status = message
            ui.redraw()
            return {'CANCELLED'}


def draw_header(box, context):
    obj = bank.active_object(context)
    b = obj.character_designer_finger_bank if obj else None
    report = _parsed(b.survey) if b and b.survey else {'warnings': {}, 'candidates': {}}
    selected = bank.selected_digits(b) if b else ()
    row = box.row(align=True)
    for digit, label in zip(detect.DIGITS, ('Th', 'I', 'M', 'R', 'P')):
        slots = [b.slots.get(f'{digit}.{side}') if b else None for side in ('L', 'R')]
        warning = report['warnings'].get(digit) or next((s.error for s in slots if s and s.error), '')
        if b and b.needs_recheck: warning = b.needs_recheck
        ready = all(s and s.guide.record and s.guide.confirmed and not s.guide.pending
                    and _parsed(s.guide.record).get('internal') for s in slots)
        partial = any(s and (s.guide.record or s.guide.pending) for s in slots)
        button = row.row(align=True)
        button.enabled, button.alert = b is not None, bool(warning)
        op = button.operator('character_designer.finger_setup', text=label,
                             icon='ERROR' if warning else 'CHECKMARK' if ready else 'QUESTION' if partial else 'RADIOBUT_OFF',
                             depress=digit in selected)
        op.action, op.digit = 'SELECT', digit
    if b and b.active:
        digit = b.active.split('.')[0]
        row = box.row(align=True)
        row.label(text=detect.LABELS[digit] if len(selected) == 1 else f'Active: {detect.LABELS[digit]} · {len(selected)} selected')
        row.operator('character_designer.finger_setup', text='', icon='FILE_REFRESH').action = 'RECHECK'
        counts = [len(report['candidates'][f'{digit}.{s}']['rings']) if f'{digit}.{s}' in report['candidates'] else '?' for s in ('L', 'R')]
        box.label(text=f'Detected rings: {counts[0]}' if counts[0] == counts[1] else f'Ring mismatch: {counts[0]} / {counts[1]}')
        errors = [b.needs_recheck, report['warnings'].get(digit, ''), b.status]
        errors += [b.slots[f'{digit}.{s}'].error for s in ('L', 'R') if b.slots.get(f'{digit}.{s}')]
        for error in dict.fromkeys(e for e in errors if e):
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
