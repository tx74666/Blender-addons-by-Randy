"""Session export queue for explicitly assigned Unity animation FBXs."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import tempfile

import bpy
from bpy.props import CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList

from . import animation_stable_fbx as stable
from .animation_worklist_queue import QueueItem, SerialQueue, SYNC


class CharacterDesignerAnimationExportRoute(PropertyGroup):
    motion_key: StringProperty()
    rig: PointerProperty(type=bpy.types.Object)
    action: PointerProperty(type=bpy.types.Action)
    slot: IntProperty()
    filepath: StringProperty(subtype='FILE_PATH')
    guid: StringProperty()

    @classmethod
    def register(cls):
        bpy.types.Scene.character_designer_animation_export_routes = CollectionProperty(type=cls)

    @classmethod
    def unregister(cls):
        del bpy.types.Scene.character_designer_animation_export_routes


@dataclass(frozen=True)
class Motion:
    scene: object
    rig: object
    action: object
    slot: int
    name: str
    motion_key: str = ''
    record: object = None

    @property
    def key(self):
        if self.motion_key:
            return f'{self.scene.session_uid}:{self.motion_key}'
        return f'{self.rig.session_uid}:{self.action.session_uid}:{self.slot}'


@dataclass
class Entry:
    motion: Motion
    error: str = ''


_queues = {}
_selections = {}
_feedback = {}
_batch = None
_STAR_STORAGE = 'character_designer_export_stars_v1'


def running():
    return _batch is not None


def _entries(scene):
    return _queues.get(scene.session_uid, {})


def _managed(scene, row, rig, action, slot, record):
    settings = scene.character_designer_animation_worklist
    if getattr(row, 'composition_json', '') or getattr(row, 'application_role', ''):
        raise ValueError('Use the linked Composition export for this motion.')
    item_key = getattr(row, 'work_item_id', '') or row.clip_key
    key = json.dumps([settings.workspace_identity or settings.workspace_path, item_key], ensure_ascii=False)
    return Motion(scene, rig, action, slot, row.name, key, record)


def _managed_motion(context, row):
    from . import animation_worklist_edit_scene as edit_scene

    scene = context.scene
    home = edit_scene.mapping_home(scene)
    if home is not None:
        if not hasattr(row, 'custom_action'):
            row = edit_scene._motion_row(home, row.clip_key) or row
        record = edit_scene.mapping_record(home, row)
        if record and record.state == 'READY' and record.controller_action:
            return _managed(home, row, record.target, record.controller_action, record.controller_slot, record)
    if getattr(row, 'custom_action', None) is not None and row.rig is not None:
        return _managed(scene, row, row.rig, row.custom_action, row.custom_slot, row)
    raise ValueError('Prepare this motion in Animation first.')


def _usable_binding(motion):
    rig, action = motion.rig, motion.action
    return (rig is not None and rig.type == 'ARMATURE' and not rig.library
            and action is not None and not action.library and not action.override_library
            and not action.asset_data and motion.scene.objects.get(rig.name) == rig
            and any(slot.handle == motion.slot and slot.target_id_type == 'OBJECT' for slot in action.slots))


def _managed_rows(settings):
    return settings.items if settings.items else settings.catalog


def _managed_choice(context, clip):
    from . import animation_neutral_motion as neutral

    if clip == neutral.ROW.clip_key and neutral.available(context.scene):
        return _managed_motion(context, neutral.ROW)
    settings = context.scene.character_designer_animation_worklist
    matches = [row for row in _managed_rows(settings) if row.clip_key == clip]
    if len(matches) != 1:
        raise ValueError('This motion is no longer available in the Animation Library.')
    return _managed_motion(context, matches[0])


def _local_choices(context):
    from . import animation, animation_action_browser as browser

    rig = animation._target(context)
    if rig is None:
        return {}
    choices = {}
    for row in browser.daily_rows(rig):
        motion = Motion(context.scene, rig, row['action'], row['slot'], row['label'])
        if row['editable'] and _usable_binding(motion):
            choices[motion.key] = motion
    return choices


def _selection(scene):
    return _selections.get(scene.session_uid, {})


def _star_key(value):
    return sha256(value.encode('utf-8')).hexdigest()[:48]


def _star_scope(scene):
    settings = scene.character_designer_animation_worklist
    return _star_key(settings.workspace_identity or settings.workspace_path) if settings.workspace_path else ''


def _stars(scene):
    return scene.get(_STAR_STORAGE, {}).get(_star_scope(scene), {})


def _library_choice(context, key, clip, local_choices, *, starred=False):
    managed = bool(context.scene.character_designer_animation_worklist.workspace_path)
    motion = None
    if clip and managed:
        motion = _managed_choice(context, clip)
    elif not clip and not managed:
        motion = local_choices.get(key)
    if motion is None or (motion.motion_key if starred else motion.key) != key or not _usable_binding(motion):
        raise ValueError('A selected export motion changed. Select it again from the Animation Library.')
    return motion


def _choose(context, key, clip):
    scene = context.scene
    token = (clip, '' if clip else key)
    selected = _selections.setdefault(scene.session_uid, {})
    if token in selected and (not key or selected[token] == key):
        del selected[token]
        return
    _library_choice(context, key, clip, {} if clip else _local_choices(context))
    selected[token] = key


def _toggle_star(context, key, clip):
    scene = context.scene
    scope = _star_scope(scene)
    if not scope or scene.library or scene.override_library:
        raise ValueError('Open a local connected animation workspace to change export stars.')
    motion = _library_choice(context, key, clip, {})
    entry_key = _star_key(clip)
    store = scene.get(_STAR_STORAGE)
    if store is None:
        scene[_STAR_STORAGE] = {}
        store = scene[_STAR_STORAGE]
    if scope not in store:
        store[scope] = {}
    stars = store[scope]
    if stars.get(entry_key, {}).get('motion_key') == motion.motion_key:
        del stars[entry_key]
    else:
        stars[entry_key] = {'clip': clip, 'motion_key': motion.motion_key}


def _validate_motion(motion):
    from . import animation_composition, animation_composition_local, animation_controller_edit
    from . import animation_pose_test, animation_preview_session, body_original_mode, unity_animation

    if not _usable_binding(motion) or motion.rig.mode == 'EDIT':
        raise ValueError('The queued character, editable Action, or Object slot is unavailable.')
    rig, action = motion.rig, motion.action
    record = motion.record
    if record is not None:
        if hasattr(record, 'controller_action'):
            if record.state != 'READY':
                raise ValueError('The queued controller motion is no longer ready.')
            current = (record.target, record.controller_action, record.controller_slot)
        else:
            current = (record.rig, record.custom_action, record.custom_slot)
        if current != (rig, action, motion.slot):
            raise ValueError('The working Action changed. Add its current version to the queue.')
    if animation_controller_edit.pending(motion.scene):
        raise ValueError('Finish preparing the character motion before exporting.')
    if (animation_preview_session.SESSION in rig or unity_animation.active_preview(rig)
            or animation_pose_test.active(rig) or body_original_mode.active(rig)
            or rig.data.pose_position == 'REST'
            or animation_composition.active(rig) or animation_composition_local.session(rig)
            or 'character_designer_rigging_session_v1' in rig):
        raise ValueError('Finish the open preview, Rigging edit, or Composition before exporting this motion.')


def _route(motion):
    for route in motion.scene.character_designer_animation_export_routes:
        if route.rig != motion.rig or route.motion_key != motion.motion_key:
            continue
        if motion.motion_key or (route.action == motion.action and route.slot == motion.slot):
            return route
    return None


def add(context, *, starred=False):
    scene = context.scene
    picks = _selection(scene)
    if starred:
        selected = [(None, item['clip'], item['motion_key']) for item in _stars(scene).values()]
        empty = 'Star at least one prepared motion in Animation first.'
    else:
        selected = [(token, token[0], key) for token, key in picks.items()]
        empty = 'Pick at least one existing animation first.'
    if not selected:
        raise ValueError(empty)
    local = {} if scene.character_designer_animation_worklist.workspace_path else _local_choices(context)
    entries = _queues.setdefault(scene.session_uid, {})
    errors = []
    for token, clip, key in selected:
        try:
            motion = _library_choice(context, key, clip, local, starred=starred)
            _validate_motion(motion)
        except (ValueError, ReferenceError) as error:
            errors.append(str(error))
        else:
            entry = entries.get(motion.key)
            if entry is None or (entry.motion.action, entry.motion.rig, entry.motion.slot) != (
                    motion.action, motion.rig, motion.slot):
                entries[motion.key] = Entry(motion)
        if token is not None:
            picks.pop(token)
    _feedback.pop(scene.session_uid, None)
    return tuple(dict.fromkeys(errors))


def _assign_destination(motion, filepath):
    _validate_motion(motion)
    path, guid, _ = stable.asset(bpy.path.abspath(filepath))
    settings = motion.scene.character_designer_animation_worklist
    if settings.model_file and Path(bpy.path.abspath(settings.model_file)).resolve() == path:
        raise ValueError('Choose the animation FBX. This is the workspace character model.')
    route = _route(motion)
    if any(other != route and other.filepath and Path(other.filepath).resolve() == path
           for scene in bpy.data.scenes for other in scene.character_designer_animation_export_routes):
        raise ValueError('This Unity FBX is already assigned to another motion.')
    if route is None:
        route = motion.scene.character_designer_animation_export_routes.add()
        route.motion_key, route.rig = motion.motion_key, motion.rig
        if not motion.motion_key:
            route.action, route.slot = motion.action, motion.slot
    route.filepath, route.guid = str(path), guid


@dataclass
class Batch:
    queue: SerialQueue
    scene: object
    window: object
    entries: dict
    job: object = None
    temporary: object = None
    destination: object = None
    reported_failures: int = 0


def _redraw():
    from .animation import _redraw as redraw
    redraw()


def begin(context):
    global _batch
    from .animation import _link_idle
    from .animation_original_inputs_ui import original_inputs_live

    entries = _entries(context.scene)
    if not entries:
        raise ValueError('Add at least one animation to the Export Queue.')
    if context.window is None or not _link_idle() or original_inputs_live(context):
        raise ValueError('Finish the current animation operation before exporting.')
    queue = SerialQueue(SYNC, (QueueItem(key) for key in entries), continue_on_failure=True)
    _batch = Batch(queue, context.scene, context.window, entries)
    registered = bpy.app.timers.is_registered(_poll)
    try:
        bpy.app.timers.register(_poll, first_interval=.05)
    except Exception:
        _batch = None
        if not registered and bpy.app.timers.is_registered(_poll):
            bpy.app.timers.unregister(_poll)
        raise
    for entry in entries.values():
        entry.error = ''
    _feedback.pop(context.scene.session_uid, None)


def _export_range(motion):
    from .animation_export_inputs import _curves

    if motion.action.use_frame_range or len(motion.action.slots) == 1:
        return tuple(motion.action.frame_range)
    ranges = [curve.range() for curve in _curves(motion.action, motion.slot)
              if not curve.mute and (curve.keyframe_points or curve.sampled_points)]
    if not ranges:
        raise ValueError('The queued Object slot has no keyed frame range.')
    return min(start for start, _ in ranges), max(end for _, end in ranges)


def _start(batch, attempt):
    from . import animation_export, animation_playback_range
    from .animation_loop_timing import is_loop

    motion = batch.entries[attempt.key].motion
    _validate_motion(motion)
    route = _route(motion)
    if route is None:
        raise ValueError('Assign an existing Unity FBX for this motion.')
    destination = stable.pin(route.filepath, route.guid)
    if any(other != route and other.filepath and Path(other.filepath).resolve() == destination.path
           for scene in bpy.data.scenes for other in scene.character_designer_animation_export_routes):
        raise ValueError('This Unity FBX is assigned to more than one motion.')
    if not batch.queue.record_fingerprint(attempt.token, destination):
        raise RuntimeError('The export queue lost its active item.')
    temporary = tempfile.TemporaryDirectory(prefix='cdesigner-unity-queue-')
    try:
        start, end = _export_range(motion)
        loop = (animation_playback_range.record_loop(motion.record)
                if motion.record is not None and hasattr(motion.record, 'controller_action')
                else is_loop(motion.action))
        layer = next((layer for layer in motion.scene.view_layers if layer.objects.get(motion.rig.name) == motion.rig), None)
        if layer is None:
            raise ValueError('The queued character is outside the available view layers.')
        with bpy.context.temp_override(window=batch.window, scene=motion.scene, view_layer=layer):
            job = animation_export.begin_export(bpy.context, motion.rig, motion.action,
                Path(temporary.name) / 'animation.fbx', frame_start=start, frame_end=end,
                loop=bool(motion.action.use_cyclic or loop), action_slot=motion.slot)
    except Exception:
        temporary.cleanup()
        raise
    batch.job, batch.temporary, batch.destination = job, temporary, destination


def _dispose(batch):
    from . import animation_export
    try:
        if batch.job is not None and animation_export.active_job() is batch.job:
            animation_export.cancel_export(batch.job)
    finally:
        if batch.temporary is not None:
            batch.temporary.cleanup()
        batch.job = batch.temporary = batch.destination = None


def _complete(batch):
    from . import animation_export

    attempt = batch.queue.running
    try:
        result = animation_export.poll_export(batch.job)
        if result is None:
            return
        stable.publish(batch.destination, result['filepath'], result['fbx_sha256'])
    except Exception as error:
        batch.queue.complete(attempt.token, success=False, message=str(error) or type(error).__name__)
    else:
        batch.queue.complete(attempt.token, success=True, result=str(batch.destination.path), published=True)
        batch.entries.pop(attempt.key, None)
    _dispose(batch)


def _poll():
    from .animation import _link_idle

    batch = _batch
    if batch is None:
        return None
    try:
        if batch.window not in tuple(bpy.context.window_manager.windows) or batch.window.scene != batch.scene:
            cancel()
        elif batch.job is not None:
            _complete(batch)
        elif _link_idle(include_collection=False):
            batch.queue.tick(lambda attempt: _start(batch, attempt))
        failures = batch.queue.failures
        for failure in failures[batch.reported_failures:]:
            batch.entries[failure.key].error = failure.message
        batch.reported_failures = len(failures)
    except (ReferenceError, ValueError, RuntimeError, OSError):
        cancel()
    if _batch is batch and not batch.queue.busy:
        _finish(batch)
    _redraw()
    return .25 if _batch is batch else None


def _finish(batch):
    global _batch
    queue = batch.queue
    try:
        message = f'Exported {len(queue.succeeded_keys)} / {queue.total} to Unity.'
        if queue.failures:
            message += f' {len(queue.failures)} failed.'
        if queue.cancel_message:
            message += ' ' + queue.cancel_message
        _feedback[batch.scene.session_uid] = message
    except ReferenceError:
        pass
    _batch = None


def cancel():
    batch = _batch
    if batch is None:
        return

    def stop_worker(attempt):
        try:
            _dispose(batch)
        except (RuntimeError, OSError) as error:
            message = 'Export stopped. ' + str(error)
        else:
            message = 'Export cancelled.'
        batch.queue.acknowledge_cancel(attempt.token, message=message)

    batch.queue.cancel(stop_worker)
    if not batch.queue.busy and not batch.queue.cancel_message:
        batch.queue.cancel_message = 'Export cancelled.'
    if not batch.queue.busy:
        _finish(batch)
        if bpy.app.timers.is_registered(_poll):
            bpy.app.timers.unregister(_poll)


def stop():
    global _batch
    if _batch is not None:
        _dispose(_batch)
        _batch = None
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    _queues.clear()
    _selections.clear()
    _feedback.clear()


class CHARACTERDESIGNER_OT_edit_animation_export_queue(Operator):
    bl_idname = 'character_designer.edit_animation_export_queue'
    bl_label = 'Edit Animation Export Queue'
    bl_options = {'INTERNAL'}

    operation: EnumProperty(items=(
        ('SELECT', 'Select for Export', 'Toggle this motion in the export selection without changing the scene Action'),
        ('ADD', 'Add Selected', 'Queue the selected working animations'),
        ('REMOVE', 'Remove', 'Remove this motion from the queue'),
        ('CLEAR', 'Clear Queue', 'Clear the temporary queue'),
        ('EXPORT', 'Export Queue to Unity', 'Update the assigned Unity animation FBXs'),
        ('CANCEL', 'Cancel', 'Cancel the batch and keep unpublished items'),
        ('ADD_STARRED', 'Add Starred', 'Queue every starred animation in this workspace'),
    ), options={'HIDDEN'})
    key: StringProperty(options={'HIDDEN'})
    clip: StringProperty(options={'HIDDEN'})

    @classmethod
    def description(cls, _context, properties):
        return cls.bl_rna.properties['operation'].enum_items[properties.operation].description

    def execute(self, context):
        try:
            if self.operation == 'CANCEL':
                cancel()
            elif running():
                raise ValueError('Wait for the Export Queue to finish or cancel it.')
            elif self.operation == 'SELECT':
                _choose(context, self.key, self.clip)
            elif self.operation in {'ADD', 'ADD_STARRED'}:
                errors = add(context, starred=self.operation == 'ADD_STARRED')
                if errors:
                    self.report({'WARNING'}, '\n'.join(errors))
            elif self.operation == 'REMOVE':
                _entries(context.scene).pop(self.key, None)
            elif self.operation == 'CLEAR':
                _queues.pop(context.scene.session_uid, None)
                _feedback.pop(context.scene.session_uid, None)
            elif self.operation == 'EXPORT':
                begin(context)
        except (ValueError, OSError, RuntimeError, ReferenceError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        _redraw()
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_toggle_animation_export_star(Operator):
    bl_idname = 'character_designer.toggle_animation_export_star'
    bl_label = 'Toggle Export Star'
    bl_description = 'Star or unstar this animation for repeated export selection'
    bl_options = {'INTERNAL', 'UNDO'}

    key: StringProperty(options={'HIDDEN'})
    clip: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        try:
            if running():
                raise ValueError('Wait for the Export Queue to finish or cancel it.')
            _toggle_star(context, self.key, self.clip)
        except (ValueError, ReferenceError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        _redraw()
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_set_animation_export_destination(Operator):
    bl_idname = 'character_designer.set_animation_export_destination'
    bl_label = 'Set Unity Animation FBX'
    bl_description = 'Choose the existing animation FBX to update when exporting this motion'
    bl_options = {'INTERNAL', 'UNDO'}

    key: StringProperty(options={'HIDDEN'})
    scene_uid: StringProperty(options={'HIDDEN'})
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.fbx', options={'HIDDEN'})

    def invoke(self, context, _event):
        entry = _entries(context.scene).get(self.key)
        if entry is None or running():
            return {'CANCELLED'}
        self.scene_uid = str(context.scene.session_uid)
        route = _route(entry.motion)
        self.filepath = route.filepath if route is not None else ''
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        entry = _entries(context.scene).get(self.key)
        if str(context.scene.session_uid) != self.scene_uid or entry is None or running():
            return {'CANCELLED'}
        try:
            _assign_destination(entry.motion, self.filepath)
        except (ValueError, OSError, RuntimeError, ReferenceError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        entry.error = ''
        _redraw()
        return {'FINISHED'}


def _button(layout, operation, title, icon, *, key=''):
    operator = layout.operator(CHARACTERDESIGNER_OT_edit_animation_export_queue.bl_idname, text=title, icon=icon)
    operator.operation, operator.key = operation, key


def _draw_choice(layout, context, name, motion, *, clip=''):
    row = layout.row(align=True)
    key = motion.key if motion is not None else ''
    token = (clip, '' if clip else key)
    selected = token in _selection(context.scene)
    row.label(text='', icon='CHECKMARK' if motion is not None else 'BLANK1')
    row.label(text=name)
    choose = row.row(align=True)
    choose.enabled = not running() and (motion is not None or selected)
    button = choose.operator(CHARACTERDESIGNER_OT_edit_animation_export_queue.bl_idname,
                             text='', icon='RESTRICT_SELECT_OFF', depress=selected)
    button.operation, button.key, button.clip = 'SELECT', key, clip


def _selectable_managed_motion(context, item):
    try:
        motion = _managed_motion(context, item)
        return motion if _usable_binding(motion) else None
    except (ValueError, ReferenceError):
        return None


def draw_managed_star(layout, context, item):
    """Draw the ready mark as a flat export-selection toggle."""
    motion = _selectable_managed_motion(context, item)
    selected = (motion is not None
                and _stars(context.scene).get(_star_key(item.clip_key), {}).get('motion_key') == motion.motion_key)
    icon = 'SOLO_ON' if selected else 'CHECKMARK'
    if motion is None or running():
        layout.label(text='', icon=icon)
        return
    button = layout.operator(CHARACTERDESIGNER_OT_toggle_animation_export_star.bl_idname,
                             text='', icon=icon, emboss=False)
    button.key, button.clip = motion.key, item.clip_key


def _draw_managed_choice(layout, context, item):
    from .animation_motion_pins import row_key

    layout.context_string_set('cd_category_motion_key', row_key(item))
    motion = _selectable_managed_motion(context, item)
    _draw_choice(layout, context, motion.name if motion is not None else item.name,
                 motion, clip=item.clip_key)


class CHARACTERDESIGNER_UL_animation_export_motions(UIList):
    def filter_items(self, context, data, propname):
        from .animation_worklist_categories import filter_rows
        return filter_rows(self, context, data, propname)

    def draw_filter(self, _context, layout):
        layout.prop(self, 'filter_name', text='', icon='VIEWZOOM')

    def draw_item(self, context, layout, _data, item, _icon, _active_data, _active_propname,
                  _index=0, _flt_flag=0):
        _draw_managed_choice(layout, context, item)


class CHARACTERDESIGNER_PT_local_export_motions(Panel):
    bl_idname = 'CHARACTERDESIGNER_PT_local_export_motions'
    bl_label = 'Choose Export Actions'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'

    def draw(self, context):
        layout = self.layout
        choices = _local_choices(context)
        for motion in choices.values():
            _draw_choice(layout, context, motion.name, motion)
        if not choices:
            layout.label(text='No exportable Actions')
        draw_add(layout, context)


def draw_library(layout, context):
    from . import animation_neutral_motion as neutral, animation_worklist_categories as categories

    settings = context.scene.character_designer_animation_worklist
    if not settings.workspace_path:
        layout.popover(panel=CHARACTERDESIGNER_PT_local_export_motions.bl_idname,
                       text='Pick from Existing Animations', icon='RESTRICT_SELECT_OFF')
        return
    categories.draw_toolbar(layout, context)
    neutral_visible = (neutral.available(context.scene)
                       and categories.category_for(context.scene, neutral.ROW)
                       == categories.current_category(context.scene))
    if neutral_visible:
        _draw_managed_choice(layout, context, neutral.ROW)
    rows = _managed_rows(settings)
    if categories.member_count(context, rows, limit=1):
        collection = 'items' if settings.items else 'catalog'
        layout.template_list(CHARACTERDESIGNER_UL_animation_export_motions.__name__, 'export',
                             settings, collection, settings, 'list_cursor', rows=5, maxrows=5)
    elif not neutral_visible:
        layout.label(text='No animations in this category')


def draw(layout, context, *, enabled=True):
    from .animation import _link_idle
    from .animation_physics_ui import section, wrapped_label

    entries = _entries(context.scene)
    box = layout.box()
    header = box.row(align=True)
    title = f'Export Queue ({len(entries)})' if entries else 'Export Queue'
    header.label(text=title)
    if entries and not running():
        clear = header.row(align=True)
        clear.enabled = enabled
        _button(clear, 'CLEAR', 'Clear Queue', 'TRASH')
    if _batch is not None and _batch.scene == context.scene:
        queue = _batch.queue
        progress = box.row(align=True)
        done = len(queue.succeeded_keys) + len(queue.failures)
        progress.label(text=f'Exporting {min(done + 1, queue.total)} / {queue.total}')
        _button(progress, 'CANCEL', '', 'X')
    if not entries:
        box.label(text='No animations queued')
    for key, entry in entries.items():
        row = box.row(align=True)
        row.enabled = enabled and not running()
        row.operator_context = 'INVOKE_DEFAULT'
        row.label(text=entry.motion.name)
        try:
            route = _route(entry.motion)
        except ReferenceError:
            route = None
        destination = row.operator(CHARACTERDESIGNER_OT_set_animation_export_destination.bl_idname,
                                   text='Set FBX…' if route is None else '', icon='FILEBROWSER')
        destination.key = key
        _button(row, 'REMOVE', '', 'X', key=key)
        if entry.error:
            wrapped_label(box, context, entry.error, icon='ERROR')
            if 'capture export base pose' in entry.error.casefold():
                rig, action = entry.motion.rig, entry.motion.action
                ad = rig.animation_data if rig is not None else None
                if (action is not None and ad is not None and ad.action == action
                        and ad.action_slot_handle == entry.motion.slot):
                    capture = box.row()
                    capture.enabled = enabled and _link_idle()
                    operator = capture.operator('character_designer.animation_export_base_pose', icon='POSE_HLT')
                    operator.rig_name, operator.action_name = rig.name, action.name
    feedback = _feedback.get(context.scene.session_uid)
    if feedback:
        box.label(text=feedback)

    if context.scene.character_designer_animation_worklist.workspace_path:
        controls = box.row()
        controls.enabled = enabled
        draw_add(controls, context, starred_only=True, show_empty=True)
        picker = section(box, 'character_designer_unity_animation_existing_picker', 'Pick from Existing Animations',
                         default_closed=True)
        if picker is not None:
            picker.enabled = enabled and not running()
            draw_library(picker, context)
            draw_add(picker, context)
    else:
        picker = box.row()
        picker.enabled = enabled and not running()
        draw_library(picker, context)

    export = box.row()
    export.enabled = enabled and bool(entries) and _link_idle()
    export.scale_y = 1.3
    _button(export, 'EXPORT', 'Export Queue to Unity', 'EXPORT')


def draw_add(layout, context, *, starred_only=False, show_empty=False):
    count = len(_stars(context.scene) if starred_only else _selection(context.scene))
    if starred_only and not count and not show_empty:
        return
    row = layout.row()
    row.enabled = bool(count) and not running()
    title = 'Add Starred to Queue' if starred_only else 'Add Selected to Queue'
    if count:
        title += f' ({count})'
    _button(row, 'ADD_STARRED' if starred_only else 'ADD', title, 'ADD')


CLASSES = (
    CharacterDesignerAnimationExportRoute,
    CHARACTERDESIGNER_OT_edit_animation_export_queue,
    CHARACTERDESIGNER_OT_toggle_animation_export_star,
    CHARACTERDESIGNER_OT_set_animation_export_destination,
    CHARACTERDESIGNER_UL_animation_export_motions,
    CHARACTERDESIGNER_PT_local_export_motions,
)
