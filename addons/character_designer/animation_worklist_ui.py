"""Explicit workspace/worklist controls, without disk reads during drawing."""

from pathlib import Path
import math
import textwrap

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup, UIList


_DRAGS = []
_SIDES = (
    ('SOURCE', 'Source', 'View the linked source Action'),
    ('CUSTOM', 'Custom', 'Edit the local custom Action'),
)


def _backend():
    from . import animation_worklist
    return animation_worklist


def _state(context):
    scene = getattr(context, 'scene', None)
    return getattr(scene, 'character_designer_animation_worklist', None)


def _idle(_context):
    from .animation import _link_idle
    return _link_idle()


def _armature(_self, obj):
    return obj.type == 'ARMATURE'


def _selected(settings):
    if settings is not None and 0 <= settings.active_index < len(settings.items):
        return settings.items[settings.active_index]
    return None


def _catalog_matches(item, search):
    text = (item.name + ' ' + item.source_name).casefold()
    return all(term in text for term in search.casefold().split())


def _active_side(item, side):
    action = item.source_action if side == 'SOURCE' else item.custom_action
    rig = item.rig
    return bool(action is not None and rig is not None and rig.animation_data is not None
                and rig.animation_data.action == action and item.side == side)


def _redraw(context):
    for area in context.screen.areas if context.screen else ():
        if area.type in {'VIEW_3D', 'DOPESHEET_EDITOR', 'GRAPH_EDITOR'}:
            area.tag_redraw()


def _drag_row_height(context):
    height = 20.0 * (context.preferences.system.ui_scale or 1.0)
    view = getattr(getattr(context, 'region', None), 'view2d', None)
    if view is not None:
        # Sidebar zoom is independent of the application DPI/UI scale.
        span = abs(view.region_to_view(0, 1)[1] - view.region_to_view(0, 0)[1])
        if math.isfinite(span) and span > 1e-6:
            height /= span
    return max(1.0, height)


def _run(operator, context, name, *args, **kwargs):
    try:
        getattr(_backend(), name)(context, *args, **kwargs)
    except (ValueError, RuntimeError, OSError, ReferenceError) as exc:
        settings = _state(context)
        if settings is not None:
            settings.status, settings.has_error = str(exc), True
        operator.report({'ERROR'}, str(exc))
        _redraw(context)
        return {'CANCELLED'}
    _redraw(context)
    return {'FINISHED'}


class CharacterDesignerAnimationWorklistCatalog(PropertyGroup):
    clip_key: StringProperty(options={'HIDDEN'})
    name: StringProperty(name='Clip')
    source_name: StringProperty(name='Source')
    link_path: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    ready: BoolProperty(name='Link Available', default=False,
                        description='Unity has provided a Link; its inputs are verified when added')


class CharacterDesignerAnimationWorklistItem(PropertyGroup):
    item_id: StringProperty(options={'HIDDEN'})
    clip_key: StringProperty(options={'HIDDEN'})
    name: StringProperty(name='Clip')
    source_action: PointerProperty(type=bpy.types.Action)
    custom_action: PointerProperty(type=bpy.types.Action)
    rig: PointerProperty(type=bpy.types.Object, poll=_armature)
    side: EnumProperty(name='Action', items=_SIDES, default='CUSTOM')
    manifest_path: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    export_rig_name: StringProperty(options={'HIDDEN'})
    model_file: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    model_sha256: StringProperty(options={'HIDDEN'})
    link_identity: StringProperty(options={'HIDDEN'})
    source_file: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    source_hash: StringProperty(options={'HIDDEN'})
    source_slot: IntProperty(default=0, options={'HIDDEN'})
    custom_slot: IntProperty(default=0, options={'HIDDEN'})
    source_data: PointerProperty(type=bpy.types.Armature)
    custom_data: PointerProperty(type=bpy.types.Armature)


class CharacterDesignerAnimationWorklistState(PropertyGroup):
    catalog: CollectionProperty(type=CharacterDesignerAnimationWorklistCatalog)
    items: CollectionProperty(type=CharacterDesignerAnimationWorklistItem)
    # Highlight only: loading a .blend or selecting a row must not swap Actions.
    active_index: IntProperty(default=-1, min=-1)
    catalog_index: IntProperty(default=-1, min=-1)
    search: StringProperty(name='Search Clips', options={'TEXTEDIT_UPDATE'})
    workspace_path: StringProperty(name='Workspace', subtype='FILE_PATH')
    workspace_id: StringProperty(options={'HIDDEN'})
    workspace_identity: StringProperty(options={'HIDDEN'})
    target_name: StringProperty(name='Character')
    target_guid: StringProperty(options={'HIDDEN'})
    model_file: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    model_sha256: StringProperty(options={'HIDDEN'})
    rig: PointerProperty(type=bpy.types.Object, poll=_armature)
    show_catalog: BoolProperty(name='Clip Browser', default=True)
    status: StringProperty(options={'SKIP_SAVE'})
    has_error: BoolProperty(options={'SKIP_SAVE'})


class CHARACTERDESIGNER_OT_worklist_connect(Operator):
    bl_idname = 'character_designer.worklist_connect'
    bl_label = 'Connect Workspace'
    bl_description = 'Open the animation workspace metadata exported by Unity'
    bl_options = {'REGISTER'}
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='character_animation_workspace.json;*.json', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _state(context) is not None and _idle(context)

    def invoke(self, context, _event):
        if not self.filepath:
            settings = _state(context)
            if settings.workspace_path:
                self.filepath = settings.workspace_path
            else:
                from .animation import unity_exchange_folder
                self.filepath = str(Path(unity_exchange_folder(context)) / 'character_animation_workspace.json')
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        return _run(self, context, 'connect', self.filepath)


class CHARACTERDESIGNER_OT_worklist_refresh(Operator):
    bl_idname = 'character_designer.worklist_refresh'
    bl_label = 'Refresh Workspace'
    bl_description = 'Refresh clip metadata and Link availability; keep the worklist and custom edits'

    @classmethod
    def poll(cls, context):
        settings = _state(context)
        return settings is not None and bool(settings.workspace_path) and _idle(context)

    def execute(self, context):
        return _run(self, context, 'refresh')


class CHARACTERDESIGNER_OT_worklist_add(Operator):
    bl_idname = 'character_designer.worklist_add'
    bl_label = 'Add to Worklist'
    bl_description = 'Verify this clip Link and prepare its Source and Custom Actions on the shared rig'
    bl_options = {'REGISTER'}
    clip_key: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _state(context) is not None and _idle(context)

    def execute(self, context):
        return _run(self, context, 'add', self.clip_key)


class CHARACTERDESIGNER_OT_worklist_activate(Operator):
    bl_idname = 'character_designer.worklist_activate'
    bl_label = 'Activate Worklist Action'
    bl_options = {'REGISTER'}
    item_id: StringProperty(options={'HIDDEN'})
    side: EnumProperty(items=_SIDES, default='CUSTOM', options={'HIDDEN'})

    @classmethod
    def description(cls, _context, properties):
        return ('View this clip\'s linked source Action on the shared character'
                if properties.side == 'SOURCE' else 'Edit this clip\'s local custom Action on the shared character')

    @classmethod
    def poll(cls, context):
        return _state(context) is not None and _idle(context)

    def execute(self, context):
        return _run(self, context, 'activate', self.item_id, side=self.side)


class CHARACTERDESIGNER_OT_worklist_remove(Operator):
    bl_idname = 'character_designer.worklist_remove'
    bl_label = 'Remove from Worklist'
    bl_description = 'Remove only this list entry; keep its Actions, source files, character and Unity assets'
    bl_options = {'REGISTER', 'UNDO'}
    item_id: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _state(context) is not None and _idle(context)

    def execute(self, context):
        return _run(self, context, 'remove', self.item_id)


class CHARACTERDESIGNER_OT_worklist_move(Operator):
    bl_idname = 'character_designer.worklist_move'
    bl_label = 'Move Worklist Entry'
    bl_description = 'Change only the saved worklist order'
    bl_options = {'REGISTER', 'UNDO'}
    item_id: StringProperty(options={'HIDDEN'})
    delta: IntProperty(default=0, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _state(context) is not None and _idle(context)

    def execute(self, context):
        return _run(self, context, 'move', self.item_id, self.delta)


class CHARACTERDESIGNER_OT_worklist_drag_handle(Operator):
    bl_idname = 'character_designer.worklist_drag_handle'
    bl_label = 'Drag Selected Entry'
    bl_description = 'Select an entry, then drag vertically inside this handle; release inside it to reorder'
    bl_options = {'UNDO', 'INTERNAL'}
    item_id: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        settings = _state(context)
        return (_selected(settings) is not None and len(settings.items) > 1
                and _idle(context) and not _DRAGS)

    def invoke(self, context, event):
        # A single tall native button keeps both endpoints inside one hit rect.
        # Blender invokes it on release and exposes the matching press position.
        if event.type != 'LEFTMOUSE' or event.value != 'RELEASE':
            return {'CANCELLED'}
        selected = _selected(_state(context))
        if selected is None or selected.item_id != self.item_id or not _idle(context):
            return {'CANCELLED'}
        press_y = getattr(event, 'mouse_prev_press_y', None)
        if press_y is None:
            return {'CANCELLED'}
        self._row_height = _drag_row_height(context)
        self._delta = round((press_y - event.mouse_y) / self._row_height)
        if not self._delta:
            return {'CANCELLED'}
        return _run(self, context, 'move', self.item_id, self._delta)


def stop_worklist_ui():
    """Cancel pointer moves during add-on refresh; no stored order was changed."""
    for operator in tuple(_DRAGS):
        operator._cancelled = True
        operator._finish()


class CHARACTERDESIGNER_OT_worklist_drag(Operator):
    bl_idname = 'character_designer.worklist_drag'
    bl_label = 'Move Entry'
    bl_description = 'Click, move the pointer vertically, then click to confirm the position; Esc cancels'
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING', 'INTERNAL'}
    item_id: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        settings = _state(context)
        return settings is not None and len(settings.items) > 1 and _idle(context) and not _DRAGS

    def _finish(self):
        workspace = getattr(self, '_workspace', None)
        if workspace is not None:
            try:
                workspace.status_text_set(None)
            except (ReferenceError, RuntimeError):
                pass
        if self in _DRAGS:
            _DRAGS.remove(self)

    def _hint(self):
        if self._workspace is not None:
            self._workspace.status_text_set(
                f'Move {self._name}: position {self._start + 1} to {self._destination + 1}. '
                'Move the pointer vertically; click to confirm; Esc to cancel.')

    def invoke(self, context, event):
        # Native operator buttons invoke on release. This is click-move-click,
        # not a press-drag gesture; keep the stable operator ID for saved callers.
        settings = _state(context)
        self._ids = tuple(item.item_id for item in settings.items)
        if self.item_id not in self._ids or len(set(self._ids)) != len(self._ids):
            self.report({'ERROR'}, 'The worklist changed; choose the entry again.')
            return {'CANCELLED'}
        self._scene = context.scene
        self._start = self._ids.index(self.item_id)
        self._destination = self._start
        self._name = settings.items[self._start].name
        self._mouse_y = event.mouse_y
        self._row_height = _drag_row_height(context)
        self._workspace = context.workspace
        self._cancelled = False
        _DRAGS.append(self)
        self._hint()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if self._cancelled or event.type in {'ESC', 'RIGHTMOUSE'}:
            self._finish()
            return {'CANCELLED'}
        settings = _state(context)
        if (context.scene != self._scene or settings is None
                or tuple(item.item_id for item in settings.items) != self._ids or not _idle(context)):
            self._finish()
            self.report({'WARNING'}, 'The scene or worklist changed; the move was cancelled.')
            return {'CANCELLED'}
        if event.type == 'MOUSEMOVE':
            offset = round((self._mouse_y - event.mouse_y) / self._row_height)
            self._destination = min(len(self._ids) - 1, max(0, self._start + offset))
            self._hint()
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._finish()
            if self._destination == self._start:
                return {'CANCELLED'}
            return _run(self, context, 'move', self.item_id, self._destination - self._start)
        return {'RUNNING_MODAL'}

    def cancel(self, _context):
        self._finish()


class CHARACTERDESIGNER_OT_worklist_sync(Operator):
    bl_idname = 'character_designer.worklist_sync'
    bl_label = 'Sync Current to Unity'
    bl_description = 'Export the active Custom Action as a new Unity candidate for Preview and Apply'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        item = _selected(_state(context))
        return item is not None and _active_side(item, 'CUSTOM') and _idle(context)

    def execute(self, context):
        return _run(self, context, 'sync')


class CHARACTERDESIGNER_UL_animation_catalog(UIList):
    def filter_items(self, _context, data, propname):
        if not data.search.strip():
            return [], []
        flags = [self.bitflag_filter_item if _catalog_matches(item, data.search) else 0
                 for item in getattr(data, propname)]
        return flags, []

    def draw_filter(self, _context, _layout):
        pass  # The shared search field filters name and source, with no display sorting.

    def draw_item(self, context, layout, data, item, _icon, _active_data,
                  _active_propname, _index=0, _flt_flag=0):
        row = layout.row(align=True)
        row.label(text=item.name, icon='ACTION')
        if item.source_name:
            row.label(text=item.source_name)
        included = any(entry.clip_key == item.clip_key for entry in data.items)
        if included:
            row.label(text='Added', icon='CHECKMARK')
        elif not item.ready:
            row.label(text='Prepare in Unity', icon='INFO')
        else:
            controls = row.row(align=True)
            controls.enabled = _idle(context)
            controls.operator('character_designer.worklist_add', text='', icon='ADD').clip_key = item.clip_key


class CHARACTERDESIGNER_UL_animation_worklist(UIList):
    def filter_items(self, _context, _data, _propname):
        return [], []  # Stored order is authoritative for buttons and drag gestures.

    def draw_filter(self, _context, _layout):
        pass

    def draw_item(self, context, layout, _data, item, _icon, _active_data,
                  _active_propname, _index=0, _flt_flag=0):
        row = layout.row(align=True)
        row.enabled = _idle(context)
        row.label(text=item.name)
        for side, label, action in (('SOURCE', 'Source', item.source_action), ('CUSTOM', 'Custom', item.custom_action)):
            cell = row.row(align=True)
            cell.enabled = action is not None
            activate = cell.operator('character_designer.worklist_activate', text=label,
                                     depress=_active_side(item, side))
            activate.item_id, activate.side = item.item_id, side
        row.operator('character_designer.worklist_remove', text='', icon='X', emboss=False).item_id = item.item_id


def draw_worklist(layout, context):
    settings = _state(context)
    if settings is None:
        layout.label(text='Animation workspace is unavailable.', icon='ERROR')
        return
    box = layout.box()
    header = box.row(align=True)
    header.label(text='Animation Worklist', icon='ACTION')
    header.operator_context = 'INVOKE_DEFAULT'
    header.operator('character_designer.worklist_connect', text='Connect', icon='FILE_FOLDER')
    header.operator('character_designer.worklist_refresh', text='', icon='FILE_REFRESH')
    if settings.workspace_path:
        box.label(text='Character: ' + (settings.target_name or 'Unnamed'))
        selected = _selected(settings)
        source = next((entry.source_name for entry in settings.catalog
                       if selected is not None and entry.clip_key == selected.clip_key), '')
        if source:
            box.label(text='Source: ' + source)
        catalog = box.row()
        catalog.prop(settings, 'show_catalog', text=f'Clip Browser ({len(settings.catalog)})', emboss=False,
                     icon='TRIA_DOWN' if settings.show_catalog else 'TRIA_RIGHT')
        if settings.show_catalog:
            box.prop(settings, 'search', text='', icon='VIEWZOOM')
            box.template_list('CHARACTERDESIGNER_UL_animation_catalog', '', settings, 'catalog',
                              settings, 'catalog_index', rows=4, maxrows=8, sort_lock=True)
            if (0 <= settings.catalog_index < len(settings.catalog)
                    and _catalog_matches(settings.catalog[settings.catalog_index], settings.search)):
                entry = settings.catalog[settings.catalog_index]
                row = box.row(align=True)
                row.label(text='Link Available' if entry.ready else 'Prepare in Unity',
                          icon='LINKED' if entry.ready else 'INFO')
                add = row.row(align=True)
                add.enabled = entry.ready and _idle(context) and not any(
                    item.clip_key == entry.clip_key for item in settings.items)
                add.operator('character_designer.worklist_add', text='Add to Worklist', icon='ADD').clip_key = entry.clip_key
        if settings.items:
            row = box.row()
            row.template_list('CHARACTERDESIGNER_UL_animation_worklist', '', settings, 'items',
                              settings, 'active_index', rows=4, maxrows=8, sort_lock=True)
            controls = row.column(align=True)
            for delta, icon in ((-1, 'TRIA_UP'), (1, 'TRIA_DOWN')):
                cell = controls.row(align=True)
                cell.enabled = (selected is not None and _idle(context)
                                and 0 <= settings.active_index + delta < len(settings.items))
                move = cell.operator('character_designer.worklist_move', text='', icon=icon)
                move.item_id, move.delta = (selected.item_id if selected is not None else ''), delta
            handle = controls.column(align=True)
            handle.operator_context = 'INVOKE_DEFAULT'
            handle.scale_y = 4.0
            handle.enabled = selected is not None and len(settings.items) > 1 and _idle(context)
            handle.operator('character_designer.worklist_drag_handle', text='', icon='GRIP').item_id = (
                selected.item_id if selected is not None else '')
            box.operator('character_designer.worklist_sync', text='Sync Current to Unity', icon='EXPORT')
        else:
            box.label(text='Add a prepared clip to start your worklist.', icon='INFO')
    if settings.status:
        width = max(24, int(getattr(context.region, 'width', 300) / (7 * (context.preferences.system.ui_scale or 1.0))) - 6)
        for index, line in enumerate(textwrap.wrap(settings.status, width=width)):
            box.label(text=line, icon=('ERROR' if settings.has_error else 'INFO') if index == 0 else 'BLANK1')


WORKLIST_CLASSES = (
    CharacterDesignerAnimationWorklistCatalog,
    CharacterDesignerAnimationWorklistItem,
    CharacterDesignerAnimationWorklistState,
    CHARACTERDESIGNER_OT_worklist_connect,
    CHARACTERDESIGNER_OT_worklist_refresh,
    CHARACTERDESIGNER_OT_worklist_add,
    CHARACTERDESIGNER_OT_worklist_activate,
    CHARACTERDESIGNER_OT_worklist_remove,
    CHARACTERDESIGNER_OT_worklist_move,
    CHARACTERDESIGNER_OT_worklist_drag_handle,
    CHARACTERDESIGNER_OT_worklist_drag,
    CHARACTERDESIGNER_OT_worklist_sync,
    CHARACTERDESIGNER_UL_animation_catalog,
    CHARACTERDESIGNER_UL_animation_worklist,
)
