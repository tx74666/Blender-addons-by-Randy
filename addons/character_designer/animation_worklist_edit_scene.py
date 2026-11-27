"""Open a prepared motion in the local scene that owns its editable Action."""

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup, UIList

EDIT_SCENE = 'character_designer_prepared_edit_scene_v1'


class CharacterDesignerControllerMotion(PropertyGroup):
    workspace_identity: StringProperty(options={'HIDDEN'})
    work_item_id: StringProperty(options={'HIDDEN'})
    clip_key: StringProperty(options={'HIDDEN'})
    target: PointerProperty(type=bpy.types.Object)
    source_rig: PointerProperty(type=bpy.types.Object)
    source_action: PointerProperty(type=bpy.types.Action)
    source_slot: IntProperty(default=0, options={'HIDDEN'})
    source_fingerprint: StringProperty(options={'HIDDEN'})
    controller_action: PointerProperty(type=bpy.types.Action)
    controller_slot: IntProperty(default=0, options={'HIDDEN'})
    # Keep old saved Action references readable for recovery.
    alternate_action: PointerProperty(type=bpy.types.Action)
    alternate_slot: IntProperty(default=0, options={'HIDDEN'})
    state: EnumProperty(items=(
        ('UNTRIED', 'Original bones', 'Controller mapping has not been attempted'),
        ('READY', 'Controllers', 'A controller Action is available for this character'),
        ('MAPPING_NEEDED', 'Mapping needed', 'Open the original motion and review the mapping issue'),
    ), default='UNTRIED', options={'HIDDEN'})
    error: StringProperty(options={'HIDDEN'})

    @classmethod
    def register(cls):
        bpy.types.Scene.character_designer_controller_motions = CollectionProperty(type=cls)

    @classmethod
    def unregister(cls):
        from . import animation_controller_edit
        animation_controller_edit.stop_runtime()
        del bpy.types.Scene.character_designer_controller_motions


def editing_scene(home):
    scene = home.get(EDIT_SCENE) if hasattr(home, 'get') else None
    if scene is None:
        return None
    if not isinstance(scene, bpy.types.Scene) or scene == home or scene.library or scene.override_library:
        return None
    owner = home.character_designer_animation_worklist
    actions = scene.character_designer_animation_worklist
    if (not owner.workspace_identity or owner.workspace_identity != actions.workspace_identity
            or not actions.rig or actions.rig == owner.rig or actions.rig.name not in scene.objects):
        return None
    return scene


def _local_row(scene, key):
    return next((row for row in scene.character_designer_animation_worklist.items if row.clip_key == key), None)


def mapping_home(scene):
    if editing_scene(scene) is not None:
        return scene
    homes = home_scenes(scene)
    return homes[0] if len(homes) == 1 else None


def mapping_record(home, row):
    if home is None or row is None:
        return None
    identity = home.character_designer_animation_worklist.workspace_identity
    target = home.character_designer_setup.rig
    key = getattr(row, 'work_item_id', '') or row.clip_key
    matches = [record for record in getattr(home, 'character_designer_controller_motions', ())
               if record.workspace_identity == identity and record.work_item_id == key
               and record.target == target]
    return matches[0] if len(matches) == 1 else None


def _motion_row(home, clip_key):
    """Resolve a UI choice without importing, activating or choosing a default."""
    scene = editing_scene(home)
    rows = [row for row in scene.character_designer_animation_worklist.items
            if row.clip_key == clip_key] if scene is not None else []
    if rows:
        return rows[0] if len(rows) == 1 else None
    rows = [row for row in home.character_designer_animation_worklist.catalog
            if row.clip_key == clip_key and row.ready]
    return rows[0] if len(rows) == 1 else None


def select_motion(home, clip_key):
    """Remember an explicit valid choice; active_index remains the bound Action."""
    if _motion_row(home, clip_key) is None:
        return
    settings = home.character_designer_animation_worklist
    settings.selected_motion_clip = clip_key
    matches = [index for index, row in enumerate(settings.catalog) if row.clip_key == clip_key]
    settings.catalog_index = matches[0] if len(matches) == 1 else -1


def selected_motion(context):
    home = mapping_home(context.scene)
    if home is None:
        return None, None
    settings = home.character_designer_animation_worklist
    if context.scene == home and settings.catalog:
        index = settings.catalog_index
        row = settings.catalog[index] if 0 <= index < len(settings.catalog) else None
        key = row.clip_key if row is not None else ''
    else:
        key = getattr(settings, 'selected_motion_clip', '')
    return home, _motion_row(home, key) if key else None


def _source_motion_row(home, clip_key):
    from . import animation_controller_edit
    row = _motion_row(home, clip_key)
    if row is None or animation_controller_edit._authored_row(row):
        return None
    if hasattr(row, 'source_action'):
        return row if row.source_action is not None and row.custom_action is not None and row.rig is not None else None
    return row if row.ready else None


def draw_controller_motion_tools(layout, context):
    from . import animation_controller_edit
    home, selected = selected_motion(context)
    if home is None:
        return
    row = _source_motion_row(home, selected.clip_key) if selected is not None else None
    record = mapping_record(home, row)
    if record is None or record.state != 'READY' or record.controller_action is None:
        return
    from . import animation_playback_range as playback
    layout.label(text=row.name, icon='ACTION')
    if context.scene == home and playback.active_record(home) == record and playback.custom_range(home, record):
        layout.operator('character_designer.reset_motion_preview_range', icon='LOOP_BACK')
    controls = layout.column()
    controls.enabled = animation_controller_edit.pending(home, row.clip_key) is None
    if animation_controller_edit._is_fk(record.controller_action):
        make = controls.operator('character_designer.prepare_controller_motion', text='Convert to IK', icon='POSE_HLT')
        make.clip_key, make.from_fk = row.clip_key, True
    if animation_controller_edit.needs_knee_alignment(record):
        align = controls.operator('character_designer.prepare_controller_motion', text='Align Knee Poles', icon='POSE_HLT')
        align.clip_key, align.align_knees = row.clip_key, True
    if animation_controller_edit.needs_elbow_alignment(record):
        align = controls.operator('character_designer.prepare_controller_motion', text='Align Elbow Poles', icon='POSE_HLT')
        align.clip_key, align.align_elbows = row.clip_key, True


def draw_source_motion(layout, context):
    home, selected = selected_motion(context)
    row = _source_motion_row(home, selected.clip_key) if selected is not None else None
    if row is None:
        return
    controls = layout.row()
    controls.enabled = getattr(context, 'mode', 'OBJECT') in {'OBJECT', 'POSE'}
    button = controls.operator('character_designer.open_source_motion', icon='ACTION')
    button.clip_key = row.clip_key


def controller_motion_active(home, row):
    record = mapping_record(home, row)
    if record is None or record.state != 'READY' or record.controller_action is None:
        return False
    rig = home.character_designer_setup.rig
    if getattr(getattr(rig, 'data', None), 'pose_position', 'POSE') == 'REST':
        return False
    animation = getattr(rig, 'animation_data', None)
    return bool(animation is not None and animation.action == record.controller_action
                and animation.action_slot_handle == record.controller_slot)


def motion_row(layout, item):
    """Tag both columns with the clicked motion, independently of the list cursor."""
    from .animation_motion_pins import row_key
    # The native UIList also copies the outer layout context to its row hit target.
    layout.context_string_set('cd_category_motion_key', row_key(item))
    row = layout.split(factor=0.66, align=True)
    return row.row(align=True), row.row(align=True)


def draw_mapping_state(layout, context, row):
    from . import animation_controller_edit
    home = mapping_home(context.scene)
    if home is None or animation_controller_edit._authored_row(row):
        available = getattr(row, 'custom_action', None) is not None
        layout.label(text='Ready' if available else 'Source', icon='CHECKMARK' if available else 'ACTION')
        return
    running = animation_controller_edit.pending(home, row.clip_key)
    if running is not None:
        label = 'Cancelling' if running['cancelling'] else ('Queued' if running['queued'] else 'Preparing')
        layout.label(text=label, icon='TIME')
        return
    record = mapping_record(home, row)
    if record is not None and record.controller_action:
        ready = record.state == 'READY'
        layout.label(text='Ready' if ready else 'Review', icon='CHECKMARK' if ready else 'ERROR')
        return
    if hasattr(row, 'source_action') and row.source_action is None:
        layout.label(text='Review', icon='ERROR')
        return
    retry = record is not None and record.state == 'MAPPING_NEEDED'
    prepare = layout.operator('character_designer.prepare_controller_motion',
                              text='Retry' if retry else 'Prepare',
                              icon='FILE_REFRESH' if retry else 'POSE_HLT')
    prepare.clip_key = row.clip_key


def _pass_eta(detail):
    sample, total = detail.get('sample', 0), detail.get('total', 0)
    if not 1 <= sample <= total:
        return ''
    elapsed = detail.get('sample_elapsed', 0)
    completed = sample - 1  # Progress is published before solving this sample.
    if completed < 4 or elapsed <= 0:
        return 'Estimating…'
    minutes, seconds = divmod(max(1, round(elapsed * (total - completed) / completed)), 60)
    return f'~{minutes}:{seconds:02d} left'


def draw_mapping_options(layout, context):
    from . import animation_controller_edit
    home = mapping_home(context.scene)
    if home is None:
        return
    settings = home.character_designer_animation_worklist
    running = animation_controller_edit.pending(home)
    if running is not None:
        box = layout.box()
        done = running['completed'] + running['failed'] + running['skipped']
        line = box.row(align=True)
        line.label(text='Preparing clips · %d / %d' % (done, running['total']), icon='POSE_HLT')
        detail = running.get('progress_detail', {})
        eta = _pass_eta(detail)
        if eta:
            line.label(text=eta)
        cancel_column = line.row(align=True)
        cancel_column.ui_units_x = 1.5
        cancel = cancel_column.operator('character_designer.cancel_controller_motion', text='', icon='X')
        cancel.job_id = running['job_id']
        # Temporary native popovers may redraw only on interaction. Keep the
        # actionable first row fixed before any changing detail below it.
        box.label(text=running['name'] or 'Waiting to start')
        minutes, seconds = divmod(running['elapsed'], 60)
        box.label(text=running['phase'] + ' · %d:%02d' % (minutes, seconds), icon='TIME')
        if detail.get('total', 0) > 0:
            sample = 'Sample %d / %d' % (detail['sample'], detail['total'])
            if detail.get('round', 0):
                sample += ' · Pass %d' % detail['round']
            box.label(text=sample)
    else:
        precise = getattr(settings, 'precise_imported_motions', False)
        count = len(animation_controller_edit.missing_motions(home))
        if precise:
            count += len(animation_controller_edit._missing_full_ik(home))
        if count:
            layout.operator('character_designer.prepare_missing_controller_motions',
                            text='Prepare Remaining Clips (%d)' % count, icon='ACTION', depress=True)
    _home, row = selected_motion(context)
    record = mapping_record(home, row)
    if record is not None and record.state == 'MAPPING_NEEDED' and record.error:
        from .animation_physics_ui import wrapped_label
        wrapped_label(layout, context, record.error, icon='INFO')
    elif settings.has_error and settings.status:
        from .animation_physics_ui import wrapped_label
        wrapped_label(layout, context, settings.status, icon='ERROR')


class CHARACTERDESIGNER_OT_reset_motion_preview_range(Operator):
    bl_idname = 'character_designer.reset_motion_preview_range'
    bl_label = 'Reset Preview Range'
    bl_description = 'Restore the automatic playback range for this prepared clip'
    bl_options = {'UNDO'}

    def execute(self, context):
        from . import animation_playback_range as playback
        record = playback.active_record(context.scene)
        if record is None:
            return {'CANCELLED'}
        playback.reset(context.scene, record)
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_prepare_controller_motion(Operator):
    bl_idname = 'character_designer.prepare_controller_motion'
    bl_label = 'Prepare Controller Animation'
    bl_description = 'Prepare this clip for editing with the character’s controls.'

    clip_key: StringProperty(options={'HIDDEN'})
    from_fk: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    align_knees: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    align_elbows: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if getattr(properties, 'align_elbows', False):
            return 'Align the elbow poles to the arm bends and retain the previous Action'
        if getattr(properties, 'align_knees', False):
            return 'Align the visible knee poles to the leg bends in a separate IK Action and keep the previous edit'
        if getattr(properties, 'from_fk', False):
            return 'Replace this working FK motion with verified IK controls'
        home = mapping_home(context.scene)
        scene = editing_scene(home) if home is not None else None
        row = _local_row(scene, properties.clip_key) if scene is not None else None
        record = mapping_record(home, row)
        if record is not None and record.state == 'MAPPING_NEEDED' and record.error:
            return 'Retry controller preparation. ' + record.error
        return cls.bl_description

    def execute(self, context):
        from . import animation_controller_edit
        home = mapping_home(context.scene)
        if home is None:
            return {'CANCELLED'}
        try:
            if getattr(self, 'align_elbows', False):
                result = animation_controller_edit.prepare_motion(context, home, self.clip_key, align_elbows=True)
            elif getattr(self, 'align_knees', False):
                result = animation_controller_edit.prepare_motion(context, home, self.clip_key, align_knees=True)
            elif getattr(self, 'from_fk', False):
                result = animation_controller_edit.prepare_motion(context, home, self.clip_key, from_fk=True)
            else:
                result = animation_controller_edit.prepare_motion(context, home, self.clip_key)
            select_motion(home, self.clip_key)
        except (ValueError, OSError, RuntimeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if result['state'] == 'READY':
            self.report({'INFO'}, result['message'])
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_prepare_missing_controller_motions(Operator):
    bl_idname = 'character_designer.prepare_missing_controller_motions'
    bl_label = 'Prepare Missing Controller Animations'
    bl_description = ('Map missing imported clips onto the existing character controls in every category; '
                      'keep existing controller edits and leave known mapping issues for explicit Retry')

    def execute(self, context):
        from . import animation_controller_edit
        home = mapping_home(context.scene)
        if home is None:
            return {'CANCELLED'}
        try:
            result = animation_controller_edit.prepare_missing(context, home)
        except (ValueError, OSError, RuntimeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if result['state'] == 'READY':
            self.report({'INFO'}, result['message'])
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_edit_prepared_motion(Operator):
    bl_idname = 'character_designer.edit_prepared_motion'
    bl_label = 'Open Motion for Editing'
    bl_description = 'Open this motion on the character; create an independent FK edit on first use'
    bl_options = {'REGISTER', 'UNDO'}

    clip_key: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        from . import animation_controller_edit
        home = context.scene
        scene = editing_scene(home)
        if scene is None or context.window is None:
            self.report({'ERROR'}, 'The editing scene for this workspace is unavailable.')
            return {'CANCELLED'}
        try:
            result = animation_controller_edit.open_motion(context, home, self.clip_key, prepare_on_open=True)
            select_motion(home, self.clip_key)
        except (ValueError, OSError, RuntimeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if result.get('state') in {'MAPPING_NEEDED', 'CONTROLLER_RECOVERY'} and result.get('message'):
            self.report({'WARNING'}, result['message'])
        elif result.get('state') == 'PREPARE_REQUIRED' and result.get('message'):
            self.report({'INFO'}, result['message'])
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_open_source_motion(Operator):
    bl_idname = 'character_designer.open_source_motion'
    bl_label = 'Edit on Source Rig'
    bl_description = 'Open the editable copy of the imported motion on its original rig'
    bl_options = {'REGISTER', 'UNDO'}

    clip_key: StringProperty(options={'HIDDEN'})

    @classmethod
    def description(cls, context, properties):
        home = mapping_home(context.scene)
        row = _source_motion_row(home, properties.clip_key) if home is not None else None
        return ('Open the editable copy of ' + row.name + ' on its original rig in the source scene.'
                if row is not None else 'Select an available imported motion first.')

    def execute(self, context):
        from . import animation_controller_edit
        home = mapping_home(context.scene)
        if home is None or _source_motion_row(home, self.clip_key) is None:
            self.report({'ERROR'}, 'This source motion is no longer available. Select an imported motion first.')
            return {'CANCELLED'}
        try:
            animation_controller_edit.open_source_motion(context, home, self.clip_key)
            select_motion(home, self.clip_key)
        except (ValueError, OSError, RuntimeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_return_from_motion(Operator):
    bl_idname = 'character_designer.return_from_motion'
    bl_label = 'Return to Character'
    home_scene: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        from . import animation_controller_edit
        home = bpy.data.scenes.get(self.home_scene)
        if home is None or editing_scene(home) != context.scene:
            self.report({'ERROR'}, 'The character scene no longer uses this editing scene.')
            return {'CANCELLED'}
        try:
            animation_controller_edit.return_to_character(context, home)
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_finish_controller_motion(Operator):
    bl_idname = 'character_designer.finish_controller_motion'
    bl_label = 'Finish Controller Motion'
    bl_options = {'INTERNAL', 'UNDO'}

    job_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        from . import animation_controller_edit
        try:
            result = animation_controller_edit.finalize_pending(context, self.job_id)
        except (ValueError, RuntimeError, OSError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'WARNING'} if result['state'] in {'MAPPING_NEEDED', 'CONTROLLER_RECOVERY'} else {'INFO'}, result['message'])
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_cancel_controller_motion(Operator):
    bl_idname = 'character_designer.cancel_controller_motion'
    bl_label = 'Cancel Controller Preparation'
    bl_description = 'Cancel the current preparation and queued motions; keep completed controller edits and original motions'

    job_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        from . import animation_controller_edit
        try:
            animation_controller_edit.cancel_pending(self.job_id)
        except (ValueError, RuntimeError, OSError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_UL_prepared_motions(UIList):
    def filter_items(self, context, data, propname):
        from . import animation_worklist_categories as categories
        return categories.filter_rows(self, context, data, propname)

    def draw_filter(self, context, layout):
        layout.prop(self, 'filter_name', text='', icon='VIEWZOOM')

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        scene = editing_scene(context.scene)
        local = _local_row(scene, item.clip_key) if scene else None
        label = local.name if local else item.name
        name_column, status = motion_row(layout, item)
        if scene and (local or item.ready):
            active = controller_motion_active(context.scene, local or item)
            button = name_column.operator('character_designer.edit_prepared_motion', text=label,
                                          icon='ACTION', emboss=False, depress=active)
            button.clip_key = item.clip_key
            draw_mapping_state(status, context, local or item)
        else:
            name_column.label(text=label, icon='ACTION')
            status.label(text='Source' if item.ready else 'Review', icon='ACTION' if item.ready else 'INFO')


def home_scenes(scene):
    return tuple(home for home in bpy.data.scenes if home != scene and home.get(EDIT_SCENE) == scene
                 and editing_scene(home) == scene)


def draw_return(layout, context):
    for home in home_scenes(context.scene):
        button = layout.operator('character_designer.return_from_motion', text='Return to ' + home.name,
                                 icon='LOOP_BACK')
        button.home_scene = home.name


CLASSES = (CharacterDesignerControllerMotion,
           CHARACTERDESIGNER_OT_reset_motion_preview_range,
           CHARACTERDESIGNER_OT_finish_controller_motion, CHARACTERDESIGNER_OT_cancel_controller_motion,
           CHARACTERDESIGNER_OT_prepare_controller_motion, CHARACTERDESIGNER_OT_prepare_missing_controller_motions,
           CHARACTERDESIGNER_OT_edit_prepared_motion,
           CHARACTERDESIGNER_OT_open_source_motion,
           CHARACTERDESIGNER_OT_return_from_motion,
           CHARACTERDESIGNER_UL_prepared_motions)
