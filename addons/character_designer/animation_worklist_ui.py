"""Explicit workspace/worklist controls, without disk reads during drawing."""

from pathlib import Path
import math
import textwrap

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup, UIList

from .animation_original_inputs_ui import (
    ORIGINAL_INPUT_CLASSES, draw_original_inputs, has_original_inputs, original_inputs_live,
)


_DRAGS = []
_SCAN_PENDING = None
_SIDES = (
    ('SOURCE', 'Source', 'View the linked source Action'),
    ('CUSTOM', 'Custom', 'Edit the local custom Action'),
)
_SCAN_STATES = (
    ('NOT_SCANNED', 'Not Scanned', 'Scan this Custom Action before choosing a batch sync'),
    ('CHANGED', 'Changed', 'The Custom Action differs from its last successful sync'),
    ('UNCHANGED', 'Unchanged', 'The Custom Action matches its last successful sync'),
    ('UNKNOWN', 'Unknown', 'The change proof is incomplete; select explicitly to sync'),
    ('BLOCKED', 'Blocked', 'This entry cannot safely sync until its reported issue is resolved'),
)
_SCAN_LABELS = {state: label for state, label, _description in _SCAN_STATES}


def _backend():
    from . import animation_worklist
    return animation_worklist


def _collection():
    from . import animation_worklist_collection as collection
    return collection


def _state(context):
    scene = getattr(context, 'scene', None)
    return getattr(scene, 'character_designer_animation_worklist', None)


def _idle(_context):
    from .animation import _link_idle
    return not scan_pending() and _link_idle()


def scan_pending():
    """A pending UI scan also blocks native Link/Export entry points."""
    global _SCAN_PENDING
    if (_SCAN_PENDING is not None and _SCAN_PENDING['armed']
            and not bpy.app.timers.is_registered(_SCAN_PENDING['callback'])):
        # Nonpersistent timers disappear on file load. Release the Python lock
        # without touching either the removed scene or the newly loaded scene.
        _SCAN_PENDING = None
    return _SCAN_PENDING is not None


def _scan_feedback(pending, message, error=False):
    try:
        settings = getattr(pending['scene'], 'character_designer_animation_worklist', None)
        if settings is not None:
            settings.status = settings.collection_status = message
            settings.has_error = error
        _redraw(pending['window'])
    except (ReferenceError, AttributeError):
        pass  # The old scene/window may already have been removed.


def _clear_pending_scan(*, unregister=True):
    global _SCAN_PENDING
    pending, _SCAN_PENDING = _SCAN_PENDING, None
    if pending is not None and unregister and pending['armed']:
        callback = pending['callback']
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    return pending


def _cancel_pending_scan(message='Scan cancelled before it started.'):
    pending = _clear_pending_scan()
    if pending is not None:
        _scan_feedback(pending, message)
    return pending is not None


def _poll_scan():
    pending = _clear_pending_scan(unregister=False)
    if pending is None:
        return None
    try:
        window, scene = pending['window'], pending['scene']
        if (window not in tuple(bpy.context.window_manager.windows) or window.scene != scene
                or scene not in tuple(bpy.data.scenes)):
            _scan_feedback(pending, 'Scan cancelled: its owning window or scene changed.')
            return None
        with bpy.context.temp_override(window=window, scene=scene):
            if not _idle(bpy.context):
                raise RuntimeError('Another animation operation started before the scan.')
            collection = _collection()
            collection.scan_changes(bpy.context)
            if pending['sync_after']:
                collection.begin_sync_changed(bpy.context, changed_only=True)
            _redraw(bpy.context)
    except Exception as exc:
        _scan_feedback(pending, str(exc), error=True)
    return None


def _defer_scan(operator, context, *, sync_after=False):
    """Give Scanning feedback one redraw before the same synchronous backend.

    This does not reduce scan CPU cost. Cancel works while pending; once the
    synchronous scan starts Blender cannot process Cancel until it returns.
    No Operator or transient Context is retained by the timer.
    """
    global _SCAN_PENDING
    if scan_pending():
        operator.report({'WARNING'}, 'A scan is already waiting to start.')
        return {'CANCELLED'}
    pending = dict(window=getattr(context, 'window', None), scene=context.scene,
                   callback=_poll_scan, armed=False, sync_after=sync_after)
    try:
        if pending['window'] is None or _state(context) is None or not _idle(context):
            raise RuntimeError('Run the scan in an idle owning Blender window.')
        _SCAN_PENDING = pending
        _scan_feedback(pending, 'Scanning animations... Blender responds when this check finishes.')
        bpy.app.timers.register(pending['callback'], first_interval=.05)
        pending['armed'] = True
    except Exception as exc:
        _clear_pending_scan()
        _scan_feedback(pending, str(exc), error=True)
        operator.report({'ERROR'}, str(exc))
        return {'CANCELLED'}
    return {'FINISHED'}


def _armature(_self, obj):
    return obj.type == 'ARMATURE'


def _selected(settings):
    if settings is not None and 0 <= settings.active_index < len(settings.items):
        return settings.items[settings.active_index]
    return None


def _catalog_matches(item, search):
    text = (item.name + ' ' + item.source_name).casefold()
    return all(term in text for term in search.casefold().split())


def _rigging_display(rig):
    if (rig is None or not hasattr(rig, 'get')
            or not rig.get('character_designer_rigging_session_v1')):
        return None
    from . import unity_animation_rigging
    display = getattr(unity_animation_rigging, 'display_state', None)
    return display(rig) if display is not None else None


def _active_side(item, side):
    action = item.source_action if side == 'SOURCE' else item.custom_action
    rig = item.rig
    if getattr(getattr(rig, 'data', None), 'pose_position', 'POSE') == 'REST':
        return False
    if action is None or rig is None or rig.animation_data is None or item.side != side:
        return False
    if rig.animation_data.action == action:
        return True
    display = _rigging_display(rig)
    return bool(display and display['rig'] is rig and display['base_action'] is action
                and display['source_read_only'] == (side == 'SOURCE')
                and rig.animation_data.use_nla
                and rig.animation_data.action in (None, display['input_action']))


def _editable_rigging(context):
    item = _selected(_state(context))
    display = _rigging_display(item.rig) if item is not None else None
    return (item, display) if (display and item.side == 'CUSTOM'
        and display['rig'] is item.rig and display['scene'] is context.scene
        and display['base_action'] is item.custom_action
        and not display['source_read_only']) else (None, None)


def _prepared_rigging_target(context):
    """Cheap display eligibility; execute reauthenticates the actual inputs."""
    settings = _state(context)
    setup = getattr(context.scene, 'character_designer_setup', None)
    main = getattr(setup, 'rig', None)
    rig = getattr(settings, 'rig', None)
    objects = getattr(context.scene, 'objects', None)
    return bool(settings is not None and settings.workspace_path and main is not None
                and rig is main and hasattr(rig, 'get')
                and getattr(rig, 'type', None) == 'ARMATURE' and rig.library is None
                and objects is not None and objects.get(rig.name) is rig
                and rig.get('character_designer_worklist_main_session_v1') is not None)


def _run_rigging(operator, context, *, edit_role=None):
    try:
        item = _selected(_state(context))
        if not _idle(context) or item is None or item.item_id != operator.item_id or item.side != 'CUSTOM':
            raise ValueError('Select the same Custom worklist entry before editing its controls.')
        from . import unity_animation_rigging
        session = unity_animation_rigging.current(item.rig)
        if (session is None or session['rig'] is not item.rig or session['scene'] is not context.scene
                or session['base_action'] is not item.custom_action or session['source_read_only']):
            raise ValueError('This entry has no editable Rigging session in the current scene.')
        _backend().validate_rigging_entry(context, item, session)
        if edit_role is not None:
            unity_animation_rigging.activate(context, session, edit_role=edit_role, enabled=True)
            action = session['base_action'] if edit_role == 'BASE' else session['input_action']
            message = 'Editing: ' + action.name
        else:
            unity_animation_rigging.key_current(context, session)
            message = 'Saved controls to "' + session['input_action'].name + '" at the current frame.'
        saved = _state(context)
        saved.status, saved.has_error = message, False
        operator.report({'INFO'}, message)
    except (ValueError, RuntimeError, OSError, ReferenceError, AttributeError, KeyError) as exc:
        saved = _state(context)
        if saved is not None:
            saved.status, saved.has_error = str(exc), True
        operator.report({'ERROR'}, str(exc))
        _redraw(context)
        return {'CANCELLED'}
    _redraw(context)
    return {'FINISHED'}


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


def _run_collection(operator, context, *names):
    try:
        collection = _collection()
        for name in names:
            getattr(collection, name)(context)
    except Exception as exc:
        settings = _state(context)
        if settings is not None:
            settings.status = settings.collection_status = str(exc)
            settings.has_error = True
        operator.report({'ERROR'}, str(exc))
        _redraw(context)
        return {'CANCELLED'}
    _redraw(context)
    return {'FINISHED'}


class CharacterDesignerAnimationWorklistCatalog(PropertyGroup):
    clip_key: StringProperty(options={'HIDDEN'})
    work_item_id: StringProperty(options={'HIDDEN'})
    name: StringProperty(name='Clip')
    source_name: StringProperty(name='Source')
    link_path: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    ready: BoolProperty(name='Link Available', default=False,
                        description='Unity has provided a Link; its inputs are verified when added')


class CharacterDesignerAnimationWorklistItem(PropertyGroup):
    workspace_path: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    workspace_identity: StringProperty(options={'HIDDEN'})
    composition_json: StringProperty(options={'HIDDEN'})
    composition_sha256: StringProperty(options={'HIDDEN'})
    application_role: StringProperty(options={'HIDDEN'})
    edit_scope: StringProperty(options={'HIDDEN'})
    b_edited: BoolProperty(default=False, options={'SKIP_SAVE'})
    composition_changed: BoolProperty(default=False, options={'SKIP_SAVE'})
    effective_inputs_changed: BoolProperty(default=False, options={'SKIP_SAVE'})
    export_needed: BoolProperty(default=False, options={'SKIP_SAVE'})
    item_id: StringProperty(options={'HIDDEN'})
    work_item_id: StringProperty(options={'HIDDEN'})
    new_animation: BoolProperty(default=False, options={'HIDDEN'})
    new_action_id: StringProperty(options={'HIDDEN'})
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
    last_synced_receipt: StringProperty(options={'HIDDEN'})
    source_baseline_receipt: StringProperty(options={'HIDDEN'})
    scan_state: EnumProperty(name='Changes', items=_SCAN_STATES, default='NOT_SCANNED',
                             options={'SKIP_SAVE'})
    scan_reason: StringProperty(options={'SKIP_SAVE'})
    sync_selected: BoolProperty(name='Sync', default=False, options={'SKIP_SAVE'},
                                description='Include this entry in the next collection sync')


class CharacterDesignerAnimationWorklistState(PropertyGroup):
    composition_workspace_path: StringProperty(name='Composition Library', subtype='FILE_PATH')
    composition_workspace_identity: StringProperty(options={'HIDDEN'})
    composition_catalog: CollectionProperty(type=CharacterDesignerAnimationWorklistCatalog)
    composition_catalog_index: IntProperty(default=-1, min=-1)
    composition_base_item_id: StringProperty(name='A Work Copy', options={'HIDDEN'})
    catalog: CollectionProperty(type=CharacterDesignerAnimationWorklistCatalog)
    items: CollectionProperty(type=CharacterDesignerAnimationWorklistItem)
    # The explicit Source/Custom buttons select entries; file load never swaps Actions.
    active_index: IntProperty(default=-1, min=-1)
    catalog_index: IntProperty(default=-1, min=-1)
    search: StringProperty(name='Search Clips', options={'TEXTEDIT_UPDATE'})
    workspace_path: StringProperty(name='Workspace', subtype='FILE_PATH')
    workspace_id: StringProperty(options={'HIDDEN'})
    prepare_request_id: StringProperty(options={'HIDDEN'})
    prepared_work_item_ids: StringProperty(options={'HIDDEN'})
    workspace_identity: StringProperty(options={'HIDDEN'})
    target_name: StringProperty(name='Character')
    target_guid: StringProperty(options={'HIDDEN'})
    model_file: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    model_sha256: StringProperty(options={'HIDDEN'})
    rig: PointerProperty(type=bpy.types.Object, poll=_armature)
    show_catalog: BoolProperty(name='Clip Browser', default=True)
    show_advanced: BoolProperty(name='Advanced', default=False, options={'SKIP_SAVE'})
    status: StringProperty(options={'SKIP_SAVE'})
    has_error: BoolProperty(options={'SKIP_SAVE'})
    collection_status: StringProperty(options={'SKIP_SAVE'})
    collection_feedback: BoolProperty(default=False, options={'SKIP_SAVE'})
    scan_completed: BoolProperty(default=False, options={'SKIP_SAVE'})


class CHARACTERDESIGNER_OT_worklist_connect(Operator):
    bl_idname = 'character_designer.worklist_connect'
    bl_label = 'Connect Workspace'
    bl_description = 'Open a Unity animation workspace or a readable .workspace.json entry'
    bl_options = {'REGISTER'}
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='character_animation_workspace.json;*.json', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _state(context) is not None and _idle(context)

    def invoke(self, context, _event):
        if not self.filepath:
            settings = _state(context)
            saved_path = settings.workspace_path
            source = Path(saved_path) if saved_path else None
            if (source is not None and source.name == 'character_animation_workspace.json'
                    and len(source.parents) >= 5 and source.parents[2].name == 'Workspaces'
                    and source.parents[3].name == 'CharacterTuning'):
                exchange = source.parents[4]
            else:
                from .animation import unity_exchange_folder
                exchange = Path(unity_exchange_folder(context))
            entries = exchange / 'Animations'
            try:
                has_entries = entries.is_dir()
            except OSError:
                has_entries = False
            if has_entries:
                self.filepath = str(entries) + '/'
            elif saved_path:
                self.filepath = saved_path
            else:
                self.filepath = str(exchange / 'character_animation_workspace.json')
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        return _run(self, context, 'connect', self.filepath)


class CHARACTERDESIGNER_OT_worklist_add_prepared_animation(Operator):
    bl_idname = 'character_designer.worklist_add_prepared_animation'
    bl_label = 'Add Prepared Animation'
    bl_description = ('Choose a Unity workspace JSON for one prepared Rigging animation; '
                      'keep this Main Rig, workspace and existing entries')
    bl_options = {'REGISTER'}
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='character_animation_workspace.json;*.workspace.json;*.json',
                               options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _prepared_rigging_target(context) and _idle(context)

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not _prepared_rigging_target(context) or not _idle(context):
            message = 'Use the same adopted Main Rig before adding a prepared Rigging animation.'
            settings = _state(context)
            if settings is not None:
                settings.status, settings.has_error = message, True
            self.report({'ERROR'}, message)
            _redraw(context)
            return {'CANCELLED'}
        return _run(self, context, 'attach_prepared_rigging_work', self.filepath)


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


class CHARACTERDESIGNER_OT_worklist_use_main_rig(Operator):
    bl_idname = 'character_designer.worklist_use_main_rig'
    bl_label = 'Use Main Rig'
    bl_description = 'Validate prepared motion on this scene Main Rig and retain a persistent artist recovery snapshot'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        settings = _state(context)
        setup = getattr(context.scene, 'character_designer_setup', None)
        return (settings is not None and bool(settings.workspace_path)
                and getattr(setup, 'rig', None) is not None and _idle(context)
                and getattr(context, 'mode', 'OBJECT') in {'OBJECT', 'POSE'})

    def execute(self, context):
        return _run(self, context, 'adopt_main_rig')


class CHARACTERDESIGNER_OT_worklist_restore_main_rig(Operator):
    bl_idname = 'character_designer.worklist_restore_main_rig'
    bl_label = 'Restore Main Rig'
    bl_description = ('Exit animation editing and restore the state before Use Main Rig; keep local drafts. '
                      'Use Rest Pose to view the resting model instead')
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        settings = _state(context)
        rig = getattr(settings, 'rig', None)
        return (rig is not None and hasattr(rig, 'get')
                and rig.get('character_designer_worklist_main_session_v1') is not None and _idle(context)
                and getattr(context, 'mode', 'OBJECT') in {'OBJECT', 'POSE'})

    def execute(self, context):
        return _run(self, context, 'restore_main_rig')


class CHARACTERDESIGNER_OT_worklist_pose_position(Operator):
    bl_idname = 'character_designer.worklist_pose_position'
    bl_label = 'Show Rest Pose or Animation Pose'
    bl_description = ('Switch the Main Rig display without changing animations or bone rest data; '
                      'choose Pose or an animation to resume')
    bl_options = {'REGISTER', 'UNDO'}

    position: EnumProperty(items=(('REST', 'Rest Pose', 'Show the undeformed resting model'),
                                 ('POSE', 'Pose', 'Show the current animation pose')))

    @classmethod
    def poll(cls, context):
        from . import animation_rest_pose
        return _idle(context) and animation_rest_pose.can_show(context)

    def execute(self, context):
        from . import animation_rest_pose
        try:
            if self.position == 'REST':
                animation_rest_pose.show_rest(context)
            else:
                animation_rest_pose.show_pose(context)
        except (ValueError, RuntimeError, AttributeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _redraw(context)
        return {'FINISHED'}


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


class CHARACTERDESIGNER_OT_worklist_add_ready(Operator):
    bl_idname = 'character_designer.worklist_add_ready'
    bl_label = 'Import Prepared'
    bl_description = (
        'Import animations prepared in Unity. Use this workspace\'s animation rig when available; '
        'otherwise create a separate animation scene and import the character. '
        'Keep existing custom edits and the active animation')

    @classmethod
    def poll(cls, context):
        settings = _state(context)
        return settings is not None and bool(settings.workspace_path) and _idle(context)

    def execute(self, context):
        return _run_collection(self, context, 'begin_add_ready')


class CHARACTERDESIGNER_OT_worklist_scan_changes(Operator):
    bl_idname = 'character_designer.worklist_scan_changes'
    bl_label = 'Scan Changes'
    bl_description = 'Check full Custom key data after redraw; scanning is synchronous and Cancel works before it starts'

    @classmethod
    def poll(cls, context):
        settings = _state(context)
        return settings is not None and bool(settings.items) and _idle(context)

    def execute(self, context):
        return _defer_scan(self, context)


class CHARACTERDESIGNER_OT_worklist_register_new(Operator):
    bl_idname = 'character_designer.worklist_register_new'
    bl_label = 'Register New Action'
    bl_description = 'Add the local new Action on this workspace rig; no original clip or target purpose is invented'

    @classmethod
    def poll(cls, context):
        settings = _state(context)
        return settings is not None and settings.rig is not None and bool(settings.workspace_path) and _idle(context)

    def execute(self, context):
        try:
            from .animation_worklist_new import register
            register(context)
        except (ValueError, RuntimeError, OSError, ReferenceError) as exc:
            settings = _state(context)
            settings.status, settings.has_error = str(exc), True
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _redraw(context)
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_worklist_sync_changed(Operator):
    bl_idname = 'character_designer.worklist_sync_changed'
    bl_label = 'Export Changes'
    bl_description = 'Check all imported animations and export only confirmed changes to Unity'

    @classmethod
    def poll(cls, context):
        settings = _state(context)
        return settings is not None and bool(settings.items) and _idle(context)

    def execute(self, context):
        # Always recheck edits. The primary export ignores old checkbox state
        # and publishes only animations with a complete change comparison.
        return _defer_scan(self, context, sync_after=True)


class CHARACTERDESIGNER_OT_worklist_cancel_collection(Operator):
    bl_idname = 'character_designer.worklist_cancel_collection'
    bl_label = 'Cancel Collection'
    bl_description = 'Stop the collection batch; keep completed updates. A running scan must finish first.'

    @classmethod
    def poll(cls, context):
        return scan_pending() or _collection().running()

    def execute(self, context):
        if _cancel_pending_scan():
            return {'FINISHED'}
        return _run_collection(self, context, 'cancel')


class CHARACTERDESIGNER_OT_worklist_activate(Operator):
    bl_idname = 'character_designer.worklist_activate'
    bl_label = 'Activate Worklist Action'
    bl_options = {'REGISTER'}
    item_id: StringProperty(options={'HIDDEN'})
    side: EnumProperty(items=_SIDES, default='CUSTOM', options={'HIDDEN'})

    @classmethod
    def description(cls, _context, properties):
        return ('View the original animation'
                if properties.side == 'SOURCE' else 'Edit your version of this animation')

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
    """Stop owned collection work and pointer moves during add-on refresh."""
    _cancel_pending_scan('Scan stopped before reload or file load.')
    _collection().stop()
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


class CHARACTERDESIGNER_OT_worklist_rigging_edit(Operator):
    bl_idname = 'character_designer.worklist_rigging_edit'
    bl_label = 'Edit Rigging Action'
    bl_description = 'Choose the actual Action that receives native edits; Base uses Blender keying'
    bl_options = {'REGISTER', 'UNDO'}
    item_id: StringProperty(options={'HIDDEN'})
    edit_role: EnumProperty(items=(('BASE', 'Base', 'Edit the base Action'),
                                   ('RIGGING', 'Controls', 'Edit the Target/Hint controls Action')),
                            default='BASE', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        item, _display = _editable_rigging(context)
        return item is not None and _idle(context)

    def execute(self, context):
        return _run_rigging(self, context, edit_role=self.edit_role)


class CHARACTERDESIGNER_OT_worklist_save_controls(Operator):
    bl_idname = 'character_designer.worklist_save_controls'
    bl_label = 'Save Controls'
    bl_description = 'Key the current effective Target/Hint inputs into their controls Action; does not export'
    bl_options = {'REGISTER', 'UNDO'}
    item_id: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        item, display = _editable_rigging(context)
        return bool(item is not None and display['enabled'] and display['edit_role'] == 'RIGGING'
                    and _idle(context))

    def execute(self, context):
        return _run_rigging(self, context)


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


def _draw_worklist_actions(layout, context, settings):
    # Plain rows avoid UIList's whole-row selection border. The Sidebar scrolls
    # the full list, and each explicit button keeps the existing active semantics.
    column = layout.column(align=True)
    column.enabled = _idle(context) and getattr(context, 'mode', 'OBJECT') in {'OBJECT', 'POSE'}
    for item in settings.items:
        row = column.row(align=True)
        for side, icon, action in (('SOURCE', 'LINKED', item.source_action),
                                   ('CUSTOM', 'EDITMODE_HLT', item.custom_action)):
            cell = row.row(align=True)
            cell.enabled = action is not None
            activate = cell.operator('character_designer.worklist_activate', text=item.name, icon=icon,
                                     depress=_active_side(item, side))
            activate.item_id, activate.side = item.item_id, side


def _draw_rigging(layout, context, item):
    if item is not None and has_original_inputs(item.rig):
        return
    display = _rigging_display(item.rig) if item is not None else None
    action = item.source_action if item is not None and item.side == 'SOURCE' else getattr(item, 'custom_action', None)
    if (not display or display['rig'] is not item.rig or display['scene'] is not context.scene
            or display['base_action'] is not action
            or display['source_read_only'] != (item.side == 'SOURCE')):
        return
    box = layout.box()
    box.label(text='Rigging', icon='ARMATURE_DATA')
    box.label(text='Base: ' + display['base_action'].name, icon='ACTION')
    box.label(text='Controls: ' + display['input_action'].name, icon='ACTION')
    actual = item.rig.animation_data.action if item.rig.animation_data else None
    editing = actual.name if actual is not None else 'Preview only'
    if display['source_read_only']:
        editing += ' (read-only)'
    box.label(text='Editing: ' + editing)
    row = box.row(align=True)
    row.enabled = _idle(context) and not display['source_read_only']
    for role, label in (('BASE', 'Edit Base'), ('RIGGING', 'Edit Controls')):
        operator = row.operator('character_designer.worklist_rigging_edit', text=label,
                                depress=display['tweak_enabled'] and display['edit_role'] == role)
        operator.item_id, operator.edit_role = item.item_id, role
    save = box.row()
    save.enabled = row.enabled and display['enabled'] and display['edit_role'] == 'RIGGING'
    save.operator('character_designer.worklist_save_controls', text='Save Controls', icon='KEY_HLT').item_id = item.item_id


def draw_worklist(layout, context):
    draw_original_inputs(layout, context)
    old_controls = layout.column()
    old_controls.enabled = not original_inputs_live(context)
    layout = old_controls
    from .animation_composition_ui import draw_composition
    draw_composition(layout, context)
    settings = _state(context)
    if settings is None:
        layout.label(text='Animation workspace is unavailable.', icon='ERROR')
        return
    box = layout.box()
    header = box.row(align=True)
    header.label(text='Animations', icon='ACTION')
    header.operator_context = 'INVOKE_DEFAULT'
    header.operator('character_designer.worklist_connect', text='Connect', icon='FILE_FOLDER')
    header.operator('character_designer.worklist_refresh', text='', icon='FILE_REFRESH')
    box.label(text='Scene: ' + getattr(context.scene, 'name', 'Unnamed'), icon='SCENE_DATA')
    busy = scan_pending() or _collection().running()
    from . import animation_rest_pose
    if animation_rest_pose.can_show(context):
        display_rig = animation_rest_pose.get_rig(context)
        pose_row = box.row(align=True)
        pose_row.enabled = _idle(context)
        for position, label in (('REST', 'Rest Pose'), ('POSE', 'Pose')):
            show = pose_row.operator('character_designer.worklist_pose_position', text=label,
                                     depress=display_rig.data.pose_position == position)
            show.position = position
    if settings.workspace_path:
        box.label(text='Character: ' + (settings.target_name or 'Unnamed'))
        setup = getattr(context.scene, 'character_designer_setup', None)
        main = getattr(setup, 'rig', None)
        rig = getattr(settings, 'rig', None)
        if main is not None:
            row = box.row(align=True)
            row.enabled = _idle(context)
            if (rig is not None and hasattr(rig, 'get')
                    and rig.get('character_designer_worklist_main_session_v1') is not None):
                row.label(text='Rig: ' + rig.name, icon='ARMATURE_DATA')
            else:
                row.operator('character_designer.worklist_use_main_rig', text='Use Main Rig', icon='ARMATURE_DATA')
        batch = box.row(align=True)
        batch.enabled = _idle(context)
        batch.operator('character_designer.worklist_add_ready', text='Import Prepared', icon='IMPORT')
        batch.operator('character_designer.worklist_sync_changed', text='Export Changes', icon='EXPORT')
        if _prepared_rigging_target(context):
            prepared = box.row()
            prepared.enabled = _idle(context)
            prepared.operator_context = 'INVOKE_DEFAULT'
            prepared.operator('character_designer.worklist_add_prepared_animation',
                              text='Add Prepared Animation', icon='ADD')
        if settings.items:
            _draw_worklist_actions(box, context, settings)
        else:
            box.label(text='Prepare motions in Unity, then click Import Prepared.', icon='INFO')
        _draw_rigging(box, context, _selected(settings))
        advanced = box.row()
        advanced.prop(settings, 'show_advanced', text='Advanced', emboss=False,
                      icon='TRIA_DOWN' if settings.show_advanced else 'TRIA_RIGHT')
        if settings.show_advanced:
            box.operator('character_designer.worklist_register_new', text='New Action', icon='ACTION')
            if (rig is not None and hasattr(rig, 'get')
                    and rig.get('character_designer_worklist_main_session_v1') is not None):
                box.operator('character_designer.worklist_restore_main_rig',
                             text='Restore Main Rig', icon='LOOP_BACK')
    if busy:
        box.operator('character_designer.worklist_cancel_collection', text='Cancel', icon='CANCEL')
    unresolved = any(
        item.scan_state in {'UNKNOWN', 'BLOCKED'} for item in settings.items)
    show_status = (settings.has_error or busy) and bool(settings.status)
    if show_status:
        width = max(24, int(getattr(context.region, 'width', 300) / (7 * (context.preferences.system.ui_scale or 1.0))) - 6)
        for index, line in enumerate(textwrap.wrap(settings.status, width=width)):
            box.label(text=line, icon=('ERROR' if settings.has_error else 'INFO') if index == 0 else 'BLANK1')
    if ((settings.has_error or busy or unresolved or getattr(settings, 'collection_feedback', False)) and settings.collection_status
            and (not show_status or settings.collection_status != settings.status)):
        width = max(24, int(getattr(context.region, 'width', 300) / (7 * (context.preferences.system.ui_scale or 1.0))) - 6)
        for index, line in enumerate(textwrap.wrap(settings.collection_status, width=width)):
            box.label(text=line, icon=('ERROR' if settings.has_error else 'INFO') if index == 0 else 'BLANK1')
    selected = _selected(settings)
    if (unresolved and selected is not None and selected.scan_state in {'UNKNOWN', 'BLOCKED'}
            and selected.scan_reason and selected.scan_reason != settings.collection_status):
        width = max(24, int(getattr(context.region, 'width', 300) / (7 * (context.preferences.system.ui_scale or 1.0))) - 6)
        for index, line in enumerate(textwrap.wrap(selected.scan_reason, width=width)):
            box.label(text=line, icon=('ERROR' if selected.scan_state == 'BLOCKED' else 'INFO') if index == 0 else 'BLANK1')


WORKLIST_CLASSES = (
    *ORIGINAL_INPUT_CLASSES,
    CharacterDesignerAnimationWorklistCatalog,
    CharacterDesignerAnimationWorklistItem,
    CharacterDesignerAnimationWorklistState,
    CHARACTERDESIGNER_OT_worklist_connect,
    CHARACTERDESIGNER_OT_worklist_add_prepared_animation,
    CHARACTERDESIGNER_OT_worklist_refresh,
    CHARACTERDESIGNER_OT_worklist_use_main_rig,
    CHARACTERDESIGNER_OT_worklist_restore_main_rig,
    CHARACTERDESIGNER_OT_worklist_pose_position,
    CHARACTERDESIGNER_OT_worklist_add,
    CHARACTERDESIGNER_OT_worklist_add_ready,
    CHARACTERDESIGNER_OT_worklist_scan_changes,
    CHARACTERDESIGNER_OT_worklist_register_new,
    CHARACTERDESIGNER_OT_worklist_sync_changed,
    CHARACTERDESIGNER_OT_worklist_cancel_collection,
    CHARACTERDESIGNER_OT_worklist_activate,
    CHARACTERDESIGNER_OT_worklist_remove,
    CHARACTERDESIGNER_OT_worklist_move,
    CHARACTERDESIGNER_OT_worklist_drag_handle,
    CHARACTERDESIGNER_OT_worklist_drag,
    CHARACTERDESIGNER_OT_worklist_sync,
    CHARACTERDESIGNER_OT_worklist_rigging_edit,
    CHARACTERDESIGNER_OT_worklist_save_controls,
    CHARACTERDESIGNER_UL_animation_catalog,
)
