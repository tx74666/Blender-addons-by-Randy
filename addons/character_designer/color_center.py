"""One character color editor, backed by the existing reversible palette.

Swatches are computed RNA properties: the character's palette JSON remains the
only saved color scheme. Opening/drawing the editor does not initialize it.
"""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty, PointerProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup

from . import bone_color_palette as palette

COLORS_PROPERTY = 'character_designer_colors'
ADVANCED_PROPERTY = 'character_designer_colors_advanced'
ERROR_PROPERTY = 'character_designer_colors_error'
BODY_GROUPS = ('BODY', 'ARMS', 'LEGS')
HAIR_GROUPS = ('HAIR_FRONT', 'HAIR_SIDE', 'HAIR_BACK', 'HAIR')
SKIRT_GROUPS = ('DRESS',)


def _highlight(normal, amount):
    return [min(1.0, value + (1.0 - value) * amount) for value in normal]


def preferences(context):
    addons = getattr(getattr(context, 'preferences', None), 'addons', {})
    entry = addons.get(__package__)
    return getattr(entry, 'preferences', None)


def default_colors(context):
    """Global proposals only; saved character colors always take precedence."""
    result = {group: palette._colors(value) for group, value in palette.DEFAULTS.items()}
    prefs = preferences(context)
    if prefs is not None:
        for group in palette.GROUPS:
            field = 'default_' + group.lower()
            if prefs.is_property_set(field):
                normal = list(getattr(prefs, field))
                result[group] = {'normal': normal, 'select': _highlight(normal, .35),
                                 'active': _highlight(normal, .70)}
    return result


def _error(context, value):
    manager = getattr(context, 'window_manager', None)
    if manager is not None and hasattr(manager, ERROR_PROPERTY):
        setattr(manager, ERROR_PROPERTY, value)


def _get_color(group, channel):
    def get(self):
        try:
            return palette.group_colors(self.id_data, group)[channel]
        except (ValueError, RuntimeError, TypeError, ReferenceError):
            return palette.DEFAULTS[group][palette.CHANNELS.index(channel)]
    return get


def _set_color(group, channel):
    def set_color(self, value):
        context = bpy.context
        try:
            main = self.id_data
            # This property belongs to an exact Object, not the active object.
            if palette.context_main(context) != main:
                raise ValueError('Open Colors for this character before editing its palette.')
            colors = palette.group_colors(main, group)
            colors[channel] = list(value)
            if channel == 'normal':
                colors['select'] = _highlight(value, .35)
                colors['active'] = _highlight(value, .70)
            palette.edit_group(main, group, colors, context)
            _error(context, '')
        except (ValueError, RuntimeError, TypeError, KeyError, ReferenceError) as exc:
            # The palette transaction has restored its state. Computed getters
            # immediately return the retained color, rather than a failed value.
            _error(context, str(exc))
        palette._redraw(context)
    return set_color


class CharacterDesignerColors(PropertyGroup):
    pass


for _group in palette.GROUPS:
    for _channel in palette.CHANNELS:
        CharacterDesignerColors.__annotations__ = getattr(CharacterDesignerColors, '__annotations__', {})
        CharacterDesignerColors.__annotations__[_channel + '_' + _group.lower()] = FloatVectorProperty(
            name={'normal': 'Color', 'select': 'Selected', 'active': 'Active'}[_channel],
            description=palette.LABELS[_group] + ' bone display color', subtype='COLOR_GAMMA',
            size=3, min=0, max=1, options=set(), get=_get_color(_group, _channel), set=_set_color(_group, _channel))


class CharacterDesignerColorPreferences(AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        layout = self.layout
        layout.label(text='Default Colors')
        layout.label(text='Used when creating a character palette.')
        for title, groups in (('Body', BODY_GROUPS), ('Hair', HAIR_GROUPS), ('Skirt', SKIRT_GROUPS)):
            layout.label(text=title)
            for group in groups:
                layout.prop(self, 'default_' + group.lower(), text=palette.LABELS[group])


for _group in palette.GROUPS:
    CharacterDesignerColorPreferences.__annotations__ = getattr(CharacterDesignerColorPreferences, '__annotations__', {})
    CharacterDesignerColorPreferences.__annotations__['default_' + _group.lower()] = FloatVectorProperty(
        name=palette.LABELS[_group], subtype='COLOR_GAMMA', size=3, min=0, max=1,
        default=palette.DEFAULTS[_group][0])


class CHARACTERDESIGNER_OT_color_preset(Operator):
    bl_idname = 'character_designer.color_preset'
    bl_label = 'Color Preset'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('APPLY', 'Apply Current Colors', 'Apply the saved character scheme'),
                                ('DEFAULT', 'Default Colors', 'Apply your global defaults to this character'),
                                ('SOFT', 'Soft Colors', 'Apply the built-in color scheme'),
                                ('RESTORE', 'Restore Previous Colors', 'Restore the colors saved before this palette')))
    rig_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return palette._poll(context)

    def execute(self, context):
        try:
            main = palette._operator_main(context, self.rig_name)
            if self.action == 'APPLY':
                palette.apply_palette(main, context)
            elif self.action == 'RESTORE':
                palette.restore_palette(main, context)
            else:
                saved = palette._read(main) or palette._default_config(context)
                saved['groups'] = (default_colors(context) if self.action == 'DEFAULT'
                                   else {key: palette._colors(value) for key, value in palette.DEFAULTS.items()})
                palette._apply(main, context, saved, palette.GROUPS)
            _error(context, '')
        except (ValueError, RuntimeError, TypeError, KeyError, ReferenceError) as exc:
            _error(context, str(exc))
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        palette._redraw(context)
        return {'FINISHED'}


def _pin(area, main):
    area.type = 'PROPERTIES'
    space = area.spaces.active
    space.pin_id = main
    space.use_pin_id = True
    space.context = 'DATA'
    area.tag_redraw()


class CHARACTERDESIGNER_OT_open_colors(Operator):
    bl_idname = 'character_designer.open_colors'
    bl_label = 'Bone Colors'
    bl_description = 'Edit this character\'s Body, Hair and Skirt bone colors'
    new_window: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return palette._poll(context) and context.window is not None

    def invoke(self, context, event):
        try:
            self._rig = palette.context_main(context)
            self._show_other = bool(palette.members(self._rig, context)['HAIR'])
            _error(context, '')
        except (ValueError, RuntimeError, TypeError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        self._dialog_open = True
        return context.window_manager.invoke_props_dialog(self, width=360, title='Bone Colors')

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        try:
            main = self._rig
            palette._read(main)
            state = getattr(main, COLORS_PROPERTY)
        except (AttributeError, ValueError, TypeError, ReferenceError) as exc:
            layout.label(text=str(exc), icon='ERROR')
            return
        hair_groups = HAIR_GROUPS if self._show_other else HAIR_GROUPS[:-1]
        for title, groups in (('Body', BODY_GROUPS), ('Hair', hair_groups), ('Skirt', SKIRT_GROUPS)):
            column = layout.column(align=True)
            column.label(text=title)
            for group in groups:
                label = ('Other' if group == 'HAIR' else
                         {'HAIR_FRONT': 'Front', 'HAIR_SIDE': 'Side', 'HAIR_BACK': 'Back'}.get(group, palette.LABELS[group]))
                column.prop(state, 'normal_' + group.lower(), text=label)
        message = getattr(context.window_manager, ERROR_PROPERTY, '')
        if message:
            layout.label(text=message, icon='ERROR')

    def execute(self, context):
        # Old callers used EXEC_DEFAULT (including the former separate-window
        # button). They now open the same popup. The dialog's confirmation is
        # the second execute call, and must close rather than reopen it.
        if getattr(self, '_dialog_open', False):
            return {'FINISHED'}
        return self.invoke(context, None)


class CHARACTERDESIGNER_PT_colors(Panel):
    bl_idname = 'CHARACTERDESIGNER_PT_colors'
    bl_label = 'Colors'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'
    bl_order = -20

    @classmethod
    def poll(cls, context):
        return False

    def draw(self, context):
        layout = self.layout
        main = palette.context_main(context)
        row = layout.row(align=True)
        row.label(text=main.name, icon='OUTLINER_OB_ARMATURE')
        row.operator('character_designer.open_colors', text='', icon='WINDOW').new_window = True
        row = layout.row(align=True)
        row.operator_menu_enum('character_designer.color_preset', 'action', text='Presets', icon='PRESET')
        row.prop(context.window_manager, ADVANCED_PROPERTY, text='Advanced', toggle=True)
        message = getattr(context.window_manager, ERROR_PROPERTY, '')
        if message:
            layout.label(text=message, icon='ERROR')


def _draw_groups(layout, context, groups):
    main = palette.context_main(context)
    state = getattr(main, COLORS_PROPERTY)
    advanced = getattr(context.window_manager, ADVANCED_PROPERTY)
    try:
        palette._read(main)
    except ValueError as exc:
        layout.label(text=str(exc), icon='ERROR')
        return
    layout.use_property_split = True
    layout.use_property_decorate = False
    for group in groups:
        label = ('Other' if group == 'HAIR' else
                 {'HAIR_FRONT': 'Front', 'HAIR_SIDE': 'Side', 'HAIR_BACK': 'Back'}.get(group, palette.LABELS[group]))
        layout.prop(state, 'normal_' + group.lower(), text=label)
        if advanced:
            col = layout.column(align=True)
            col.prop(state, 'select_' + group.lower(), text='Selected')
            col.prop(state, 'active_' + group.lower(), text='Active')


class CHARACTERDESIGNER_PT_colors_body(Panel):
    bl_idname = 'CHARACTERDESIGNER_PT_colors_body'
    bl_label = 'Body'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'
    bl_parent_id = 'CHARACTERDESIGNER_PT_colors'
    def draw(self, context):
        _draw_groups(self.layout, context, BODY_GROUPS)


class CHARACTERDESIGNER_PT_colors_hair(Panel):
    bl_idname = 'CHARACTERDESIGNER_PT_colors_hair'
    bl_label = 'Hair'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'
    bl_parent_id = 'CHARACTERDESIGNER_PT_colors'
    def draw(self, context):
        _draw_groups(self.layout, context, HAIR_GROUPS)


class CHARACTERDESIGNER_PT_colors_skirt(Panel):
    bl_idname = 'CHARACTERDESIGNER_PT_colors_skirt'
    bl_label = 'Skirt'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'
    bl_parent_id = 'CHARACTERDESIGNER_PT_colors'
    def draw(self, context):
        _draw_groups(self.layout, context, SKIRT_GROUPS)


COLOR_CENTER_CLASSES = (CharacterDesignerColors, CharacterDesignerColorPreferences,
                        CHARACTERDESIGNER_OT_color_preset, CHARACTERDESIGNER_OT_open_colors,
                        CHARACTERDESIGNER_PT_colors, CHARACTERDESIGNER_PT_colors_body,
                        CHARACTERDESIGNER_PT_colors_hair, CHARACTERDESIGNER_PT_colors_skirt)


def register_properties():
    if not hasattr(bpy.types.Object, COLORS_PROPERTY):
        setattr(bpy.types.Object, COLORS_PROPERTY, PointerProperty(type=CharacterDesignerColors))
    if not hasattr(bpy.types.WindowManager, ADVANCED_PROPERTY):
        setattr(bpy.types.WindowManager, ADVANCED_PROPERTY, BoolProperty(default=False, options={'SKIP_SAVE'}))
    if not hasattr(bpy.types.WindowManager, ERROR_PROPERTY):
        setattr(bpy.types.WindowManager, ERROR_PROPERTY, StringProperty(options={'SKIP_SAVE'}))


def unregister_properties():
    for owner, name in ((bpy.types.Object, COLORS_PROPERTY),
                        (bpy.types.WindowManager, ADVANCED_PROPERTY),
                        (bpy.types.WindowManager, ERROR_PROPERTY)):
        if hasattr(owner, name):
            delattr(owner, name)
