"""Manage local single-frame Pose assets from the Current File Asset Browser."""

import os
from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy.types import Menu, Operator, Panel

from . import control_pose_assets as poses, control_pose_capture as capture
from .animation_retarget import _curves


_GROUP_PREFIX = 'Pose Group: '


def _absolute(path):
    return Path(os.path.normcase(os.path.abspath(bpy.path.abspath(path))))


def current_file_params(context):
    space = getattr(context, 'space_data', None)
    if space is None or space.type != 'FILE_BROWSER' or space.browse_mode != 'ASSETS':
        return None
    params = space.params
    if params is None or params.asset_library_reference != 'LOCAL':
        return None
    # Current File can also be selected while editing a shared library file itself.
    if bpy.data.filepath and any(
            entry.name == capture._SHARED_LIBRARY and entry.path
            and not getattr(entry, 'use_remote_url', False)
            and _absolute(bpy.data.filepath).is_relative_to(_absolute(entry.path))
            for entry in context.preferences.filepaths.asset_libraries):
        return None
    return params


def _local_asset(action):
    return (isinstance(action, bpy.types.Action) and action.asset_data is not None
            and action.library is None and action.override_library is None
            and action.is_editable and not action.is_runtime_data)


def _is_pose(action):
    if not _local_asset(action):
        return False
    slots = getattr(action, 'slots', ())
    if slots and (len(slots) != 1 or slots[0].target_id_type != 'OBJECT'):
        return False
    curves = _curves(action)
    if not curves:
        return False
    frame = None
    for curve in curves:
        path = poses._PATH.fullmatch(curve.data_path)
        if (path is None or not 0 <= curve.array_index < poses._FIELDS.get(path[2], 0)
                or curve.modifiers or len(curve.keyframe_points) != 1):
            return False
        point_frame = round(curve.keyframe_points[0].co.x, 6)
        if frame is not None and point_frame != frame:
            return False
        frame = point_frame
    return True


def _selected_pose(context):
    asset = getattr(context, 'asset', None)
    action = asset.local_id if asset is not None and asset.id_type == 'ACTION' else None
    return action if _is_pose(action) else None


def _group_tags(action):
    return [tag for tag in action.asset_data.tags if tag.name.startswith(_GROUP_PREFIX)]


def _group(action):
    tags = _group_tags(action)
    return tags[0].name.removeprefix(_GROUP_PREFIX) if tags else ''


def _set_group(action, group):
    tags = action.asset_data.tags
    old = _group_tags(action)
    name = _GROUP_PREFIX + group if group else ''
    if name and not any(tag.name == name for tag in old):
        tags.new(name)
    for tag in reversed(old):
        if tag.name != name:
            tags.remove(tag)


def _refresh_browser(context):
    try:
        if bpy.ops.asset.library_refresh.poll():
            bpy.ops.asset.library_refresh()
    except RuntimeError:
        pass
    if context.area is not None:
        context.area.tag_redraw()


def _has_users(action):
    return action.users > int(action.use_fake_user)


class _PoseTarget:
    """Bind dialogs to the original Action across selection changes."""

    @classmethod
    def poll(cls, context):
        return current_file_params(context) is not None and _selected_pose(context) is not None

    def _capture(self, action):
        self.action_name = action.name
        self.action_uid = str(action.session_uid)

    def _resolve(self, context):
        action = bpy.data.actions.get(self.action_name)
        if (current_file_params(context) is None or not _is_pose(action)
                or str(action.session_uid) != self.action_uid):
            raise ValueError('The selected local Pose changed. Select it again.')
        return action


class CHARACTERDESIGNER_OT_organize_local_pose(_PoseTarget, Operator):
    bl_idname = 'character_designer.organize_local_pose'
    bl_label = 'Organize Pose'
    bl_description = 'Rename this local Pose or assign it to a group in the current project'
    bl_options = {'UNDO'}

    action_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    action_uid: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    name: StringProperty(name='Name', maxlen=63, options={'SKIP_SAVE'})
    group: StringProperty(name='Group', maxlen=40, options={'SKIP_SAVE'},
                          description='Choose a group such as Hands or Full Body. Leave empty to clear it')

    def invoke(self, context, _event):
        action = _selected_pose(context)
        if action is None:
            return {'CANCELLED'}
        self._capture(action)
        self.name, self.group = action.name, _group(action)
        return context.window_manager.invoke_props_dialog(self, width=310)

    def draw(self, _context):
        self.layout.prop(self, 'name')
        self.layout.prop(self, 'group')

    def execute(self, context):
        try:
            action = self._resolve(context)
            name = self.name.strip()
            if not name or len(name.encode('utf-8')) > 63:
                raise ValueError('Enter a Pose name of at most 63 UTF-8 bytes.')
            if name != action.name and any(other.library is None and other.name == name
                                           for other in bpy.data.actions):
                raise ValueError('Another local Action already has this name.')
            group = ' '.join(self.group.split())
            if len(group.encode('utf-8')) > 40:
                raise ValueError('Enter a group of at most 40 UTF-8 bytes.')
            _set_group(action, group)
            if action.name != name:
                action.name = name
        except (ValueError, RuntimeError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        _refresh_browser(context)
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_delete_local_pose(_PoseTarget, Operator):
    bl_idname = 'character_designer.delete_local_pose'
    bl_label = 'Delete Pose'
    bl_description = 'Delete an unused Pose Action from the current project'
    bl_options = {'UNDO'}

    action_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    action_uid: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        action = _selected_pose(context)
        if action is None:
            return {'CANCELLED'}
        if _has_users(action):
            self.report({'WARNING'}, 'This Pose Action is in use.')
            return {'CANCELLED'}
        self._capture(action)
        return context.window_manager.invoke_confirm(
            self, event, title='Delete Pose?', confirm_text='Delete Pose',
            message=f'Remove “{action.name}” from this project?')

    def execute(self, context):
        try:
            action = self._resolve(context)
            if _has_users(action):
                raise ValueError('This Pose Action is in use.')
            # Keep Blender's last native user check and never unlink animation.
            bpy.data.actions.remove(action, do_unlink=False)
        except (ValueError, RuntimeError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        _refresh_browser(context)
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_filter_pose_group(Operator):
    bl_idname = 'character_designer.filter_pose_group'
    bl_label = 'Filter Pose Group'
    bl_description = 'Search the Current File thumbnails by Pose group'
    bl_options = {'INTERNAL'}

    group: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return current_file_params(context) is not None

    def execute(self, context):
        params = current_file_params(context)
        if params is None:
            return {'CANCELLED'}
        params.filter_search = _GROUP_PREFIX + self.group if self.group else ''
        if self.group:
            params.asset_catalog_visibility = 'ALL'
            params.filter_asset_id.filter_action = True
        if context.area is not None:
            context.area.tag_redraw()
        return {'FINISHED'}


class CHARACTERDESIGNER_MT_pose_groups(Menu):
    bl_idname = 'CHARACTERDESIGNER_MT_pose_groups'
    bl_label = 'Pose Groups'

    def draw(self, _context):
        layout = self.layout
        layout.operator('character_designer.filter_pose_group', text='Clear Search', icon='X').group = ''
        groups = {_group(action) for action in bpy.data.actions if _local_asset(action)}
        for group in sorted(groups - {''}, key=str.casefold):
            layout.operator('character_designer.filter_pose_group', text=group, icon='TAG').group = group


class CHARACTERDESIGNER_PT_local_pose_library(Panel):
    bl_idname = 'CHARACTERDESIGNER_PT_local_pose_library'
    bl_label = 'Pose Library'
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_order = -10

    @classmethod
    def poll(cls, context):
        return current_file_params(context) is not None

    def draw(self, context):
        layout = self.layout
        layout.operator_context = 'INVOKE_DEFAULT'
        row = layout.row(align=True)
        row.operator('character_designer.save_pose_asset', text='Save Pose',
                     icon='ADD').current_file_only = True
        row.menu(CHARACTERDESIGNER_MT_pose_groups.bl_idname, text='Groups', icon='TAG')
        action = _selected_pose(context)
        if action is None:
            layout.label(text='Select a pose to manage.')
            return
        group = _group(action)
        if group:
            layout.label(text=group, icon='TAG')
        layout.operator('character_designer.apply_control_pose', icon='POSE_HLT')
        row = layout.row(align=True)
        row.operator('character_designer.organize_local_pose', text='Organize…', icon='EDITMODE_HLT')
        delete = row.row(align=True)
        delete.enabled = not _has_users(action)
        delete.operator('character_designer.delete_local_pose', text='Delete…', icon='TRASH')
        if not delete.enabled:
            layout.label(text='Pose is in use.', icon='INFO')


LOCAL_POSE_LIBRARY_CLASSES = (
    CHARACTERDESIGNER_OT_organize_local_pose,
    CHARACTERDESIGNER_OT_delete_local_pose,
    CHARACTERDESIGNER_OT_filter_pose_group,
    CHARACTERDESIGNER_MT_pose_groups,
    CHARACTERDESIGNER_PT_local_pose_library,
)
