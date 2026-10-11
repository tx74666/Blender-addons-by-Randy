"""Compact explicit Composition controls; drawing performs no file reads."""
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator, UIList


def _backend():
    from . import animation_composition
    return animation_composition


def _state(context):
    return getattr(context.scene, 'character_designer_animation_worklist', None)


def _selected(context):
    saved = _state(context)
    return saved.items[saved.active_index] if saved and 0 <= saved.active_index < len(saved.items) else None


def _idle(context):
    from .animation import _link_idle
    return _state(context) is not None and _link_idle()


def _run(operator, context, callback):
    try:
        callback()
    except (ValueError, OSError, RuntimeError, ReferenceError, AttributeError) as error:
        saved = _state(context)
        if saved:
            saved.status, saved.has_error = str(error), True
        operator.report({'ERROR'}, str(error))
        return {'CANCELLED'}
    for area in context.screen.areas if context.screen else ():
        area.tag_redraw()
    return {'FINISHED'}


class CHARACTERDESIGNER_OT_composition_connect(Operator):
    bl_idname = 'character_designer.composition_connect'
    bl_label = 'Connect Composition Library'
    bl_description = 'Connect a separately prepared composition library while retaining existing animations'
    bl_options = {'REGISTER'}
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='character_animation_workspace.json;*.workspace.json', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _idle(context)

    def invoke(self, context, _event):
        saved = _state(context)
        if not self.filepath:
            self.filepath = saved.composition_workspace_path or str(Path(saved.workspace_path).parent)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        return _run(self, context, lambda: _backend().connect_library(context, self.filepath))


class CHARACTERDESIGNER_OT_composition_add(Operator):
    bl_idname = 'character_designer.composition_add'
    bl_label = 'Add Overlay'
    bl_description = 'Add this prepared overlay Source/Custom pair on the existing Main Rig'
    bl_options = {'REGISTER', 'UNDO'}
    clip_key: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _idle(context)

    def execute(self, context):
        saved = _state(context)
        key = self.clip_key
        if not key and 0 <= saved.composition_catalog_index < len(saved.composition_catalog):
            key = saved.composition_catalog[saved.composition_catalog_index].clip_key
        return _run(self, context, lambda: _backend().add_work_item(context, key))


class CHARACTERDESIGNER_OT_composition_open(Operator):
    bl_idname = 'character_designer.composition_open'
    bl_label = 'Open Composition'
    bl_description = 'View the protected base reference while editing a separate overlay work copy'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        item = _selected(context)
        return _idle(context) and item is not None and bool(item.composition_json)

    def execute(self, context):
        item = _selected(context)
        return _run(self, context, lambda: _backend().enter(context, _backend().item_spec(item), item.item_id))


class CHARACTERDESIGNER_OT_composition_create_a(Operator):
    bl_idname = 'character_designer.composition_create_a'
    bl_label = 'Create Base Work Copy'
    bl_description = 'Create a separate New Action from the prepared base Source Link; its reference remains read-only'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        item = _selected(context)
        return (_idle(context) and item is not None and bool(item.composition_json)
                and not _backend().active(item.rig))

    def execute(self, context):
        item = _selected(context)
        return _run(self, context, lambda: _backend().create_reference_work_copy(context, item.item_id))


class CHARACTERDESIGNER_OT_composition_refresh(Operator):
    bl_idname = 'character_designer.composition_refresh'
    bl_label = 'Refresh Composition'
    bl_description = 'Accept a prepared reference/configuration revision without changing overlay curves'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        item = _selected(context)
        return _idle(context) and item is not None and bool(item.composition_json)

    def execute(self, context):
        return _run(self, context, lambda: _backend().refresh_configuration(context))


_A_ITEMS = []


def _base_items(_operator, context):
    global _A_ITEMS
    saved = _state(context)
    rig = saved.rig if saved else None
    if not _backend().active(rig):
        return [('NONE', 'No Base Work Copy', 'Open Composition first')]
    session = _backend()._load(rig)
    links = _backend()._host('animation_link')
    _A_ITEMS = [(item.item_id, item.name, 'Edit this local base Custom work item')
        for item in saved.items if item.custom_action is not None
        and item.custom_action is not session['custom_action']
        and item.custom_action is not session['base_action']
        and item.custom_action.library is None
        and item.custom_action.get(links.PACKAGE_HASH_KEY, '').lower() == session['spec']['referencePacketSha256']]
    return _A_ITEMS or [('NONE', 'No Base Work Copy', 'Add the exact local base Custom work item first')]


class CHARACTERDESIGNER_OT_composition_target(Operator):
    bl_idname = 'character_designer.composition_target'
    bl_label = 'Choose Editing Target'
    bl_description = 'Choose the overlay or base Custom work copy; the protected base reference stays read-only'
    bl_options = {'REGISTER', 'UNDO'}
    target: EnumProperty(items=[('B', 'Edit Overlay', ''), ('A', 'Edit Base Work Copy', '')], default='B')
    base_item_id: EnumProperty(name='Base Work Copy', items=_base_items)

    @classmethod
    def poll(cls, context):
        saved = _state(context)
        return _idle(context) and saved is not None and _backend().active(saved.rig)

    def invoke(self, context, _event):
        if self.target == 'A':
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, _context):
        self.layout.prop(self, 'base_item_id')

    def execute(self, context):
        return _run(self, context, lambda: _backend().set_edit_target(context, self.target, base_item_id=self.base_item_id))


class CHARACTERDESIGNER_OT_composition_view(Operator):
    bl_idname = 'character_designer.composition_view'
    bl_label = 'Composition View'
    bl_description = 'Switch the view without writing either Action'
    bl_options = {'REGISTER', 'UNDO'}
    solo: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        saved = _state(context)
        return _idle(context) and saved is not None and _backend().active(saved.rig)

    def execute(self, context):
        return _run(self, context, lambda: _backend().set_solo(context, self.solo))


class CHARACTERDESIGNER_OT_composition_capture_hold(Operator):
    bl_idname = 'character_designer.composition_capture_hold'
    bl_label = 'Save Pose'
    bl_description = 'Save the current supported overlay controls as a constant Hold pose before syncing or closing'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        saved = _state(context)
        if not _idle(context) or saved is None or not _backend().active(saved.rig):
            return False
        try:
            session = _backend()._load(saved.rig)
            return (session['edit_target'] == 'B' and session['spec']['timeMode'] == 'HoldPose'
                    and saved.rig.animation_data is not None and saved.rig.animation_data.use_tweak_mode)
        except (ValueError, ReferenceError, AttributeError, KeyError, TypeError):
            return False

    def execute(self, context):
        return _run(self, context, lambda: _backend().capture_hold_pose(context))


class CHARACTERDESIGNER_OT_composition_resume(Operator):
    bl_idname = 'character_designer.composition_resume'
    bl_label = 'Resume Editing'
    bl_description = 'Validate and resume the saved native composition editing session'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        saved = _state(context)
        return _idle(context) and saved is not None and _backend().active(saved.rig)

    def execute(self, context):
        return _run(self, context, lambda: _backend().resume(context))


class CHARACTERDESIGNER_OT_composition_close(Operator):
    bl_idname = 'character_designer.composition_close'
    bl_label = 'Close Composition'
    bl_description = 'Commit the explicit editing target, restore the artist state and retain both native drafts'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        saved = _state(context)
        return _idle(context) and saved is not None and _backend().active(saved.rig)

    def execute(self, context):
        return _run(self, context, lambda: _backend().exit(context))


class CHARACTERDESIGNER_OT_composition_sync(Operator):
    bl_idname = 'character_designer.composition_sync'
    bl_label = 'Sync Overlay'
    bl_description = 'Export the isolated overlay with its prepared role and composition proof'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        item = _selected(context)
        if not _idle(context) or item is None or not item.composition_json:
            return False
        saved = _state(context)
        try:
            if _backend().active(saved.rig):
                session = _backend()._load(saved.rig)
                return (session['edit_target'] == 'B' and session['edit_item_id'] == item.item_id
                        and session['rig'] is item.rig)
            return True
        except (ValueError, ReferenceError, AttributeError, KeyError, TypeError):
            return False

    def execute(self, context):
        return _run(self, context, lambda: _backend().begin_sync(context))


class CHARACTERDESIGNER_OT_composition_cancel_sync(Operator):
    bl_idname = 'character_designer.composition_cancel_sync'
    bl_label = 'Cancel Sync Overlay'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        job = _backend()._host('animation_export').active_job()
        return bool(job and job.get('composition_job'))

    def execute(self, context):
        return _run(self, context, lambda: _backend().cancel_sync(context))


class CHARACTERDESIGNER_UL_composition_catalog(UIList):
    def draw_item(self, context, layout, data, item, _icon, _active_data, _active_propname, _index=0, _flt_flag=0):
        row = layout.row(align=True)
        row.label(text=item.name, icon='ACTION')
        if any(entry.clip_key == item.clip_key for entry in data.items):
            row.label(text='Added', icon='CHECKMARK')
        elif not item.ready:
            row.label(text='Prepare in Unity', icon='INFO')
        else:
            row.operator('character_designer.composition_add', text='', icon='ADD').clip_key = item.clip_key


class CHARACTERDESIGNER_OT_composition_weapon(Operator):
    bl_idname = 'character_designer.composition_weapon'
    bl_label = 'Attachment Display'
    bl_description = 'Load or remove the captured attachment reference without changing existing rigging'
    bl_options = {'REGISTER', 'UNDO'}
    remove: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        saved = _state(context)
        return _idle(context) and saved is not None and saved.rig is not None

    def execute(self, context):
        return _run(self, context, lambda: (_backend().remove_weapon(context) if self.remove else _backend().load_weapon(context)))


class CHARACTERDESIGNER_OT_composition_register_b(Operator):
    bl_idname = 'character_designer.composition_register_b'
    bl_label = 'Register Overlay'
    bl_description = 'Register the separate active local Action using this prepared overlay role and bind-skeleton template'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        selected = _selected(context)
        return _idle(context) and selected is not None and bool(selected.composition_json)

    def execute(self, context):
        return _run(self, context, lambda: _backend().register_b(context))


def draw_composition(layout, context):
    saved = _state(context)
    if saved is None or not saved.workspace_path:
        return
    backend, selected = _backend(), _selected(context)
    box = layout.box()
    header = box.row(align=True)
    header.label(text='Composition', icon='NLA')
    header.operator_context = 'INVOKE_DEFAULT'
    header.operator('character_designer.composition_connect', text='Library', icon='FILE_FOLDER')
    session = None
    if backend.active(saved.rig):
        try:
            session = backend._load(saved.rig)  # native metadata only, no I/O
        except ValueError as error:
            box.label(text=str(error), icon='ERROR')
            return
    if session is not None:
        box.label(text='Base: ' + session['base_action'].name, icon='ACTION')
        box.label(text='Editing: ' + (session['custom_action'].name if session['edit_target'] == 'B'
                                     else session['base_custom_action'].name), icon='ACTION')
        box.label(text=session['spec']['timeMode'] + ' · Upper Body Override')
        row = box.row(align=True)
        row.operator('character_designer.composition_target', text='Edit Overlay', depress=session['edit_target'] == 'B').target = 'B'
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator('character_designer.composition_target', text='Edit Base', depress=session['edit_target'] == 'A').target = 'A'
        row = box.row(align=True)
        row.operator('character_designer.composition_view', text='Result', depress=not session['solo']).solo = False
        row.operator('character_designer.composition_view', text=('Overlay Only' if session['edit_target'] == 'B' else 'Base Only'), depress=session['solo']).solo = True
        if session['edit_target'] == 'B' and session['spec']['timeMode'] == 'HoldPose':
            box.operator('character_designer.composition_capture_hold', icon='KEY_HLT')
        if not saved.rig.animation_data.use_tweak_mode:
            box.operator('character_designer.composition_resume', icon='PLAY')
        row = box.row(align=True)
        row.operator('character_designer.composition_sync', text='Sync Overlay', icon='EXPORT')
        row.operator('character_designer.composition_close', text='Close', icon='LOOP_BACK')
        if session.get('following_hold'):
            box.label(text='Following Hold: ' + session['following_hold']['custom_action'].name, icon='ACTION')
        row = box.row(align=True)
        row.operator('character_designer.composition_weapon', text='Load Attachment', icon='IMPORT').remove = False
    else:
        if saved.composition_catalog:
            box.template_list('CHARACTERDESIGNER_UL_composition_catalog', 'composition', saved,
                'composition_catalog', saved, 'composition_catalog_index', rows=2, maxrows=4)
            box.operator('character_designer.composition_add', text='Add Overlay', icon='ADD')
        if selected is not None and selected.composition_json:
            box.label(text='Overlay: ' + selected.name, icon='ACTION')
            row = box.row(align=True)
            row.operator('character_designer.composition_open', icon='NLA')
            row.operator('character_designer.composition_refresh', text='', icon='FILE_REFRESH')
            box.operator('character_designer.composition_create_a', icon='DUPLICATE')
            box.operator('character_designer.composition_sync', text='Sync Overlay', icon='EXPORT')
            box.operator('character_designer.composition_register_b', text='Register Overlay', icon='ACTION')
    if saved.rig is not None and saved.rig.get('character_designer_composition_weapon_v1'):
        box.operator('character_designer.composition_weapon', text='Remove Attachment', icon='X').remove = True
        try:
            import json
            proof = json.loads(saved.rig['character_designer_composition_weapon_v1']['proof_json'])
            if proof.get('grip_limitation'):
                box.label(text=proof['grip_limitation'], icon='INFO')
        except (ValueError, TypeError, KeyError):
            box.label(text='The captured attachment needs validation before editing.', icon='ERROR')
    job = backend._host('animation_export').active_job()
    if job and job.get('composition_job'):
        box.operator('character_designer.composition_cancel_sync', icon='CANCEL')


COMPOSITION_CLASSES = (CHARACTERDESIGNER_OT_composition_connect,
    CHARACTERDESIGNER_OT_composition_add, CHARACTERDESIGNER_OT_composition_open,
    CHARACTERDESIGNER_OT_composition_create_a,
    CHARACTERDESIGNER_OT_composition_refresh, CHARACTERDESIGNER_OT_composition_target,
    CHARACTERDESIGNER_OT_composition_view, CHARACTERDESIGNER_OT_composition_capture_hold,
    CHARACTERDESIGNER_OT_composition_resume, CHARACTERDESIGNER_OT_composition_close,
    CHARACTERDESIGNER_OT_composition_sync, CHARACTERDESIGNER_OT_composition_cancel_sync,
    CHARACTERDESIGNER_UL_composition_catalog, CHARACTERDESIGNER_OT_composition_weapon,
    CHARACTERDESIGNER_OT_composition_register_b)
