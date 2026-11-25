"""Persist Blender's captured preview on shared Actions written by Save Pose."""

from array import array
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy

from . import control_pose_capture as capture


def _selected_capture(context):
    asset = getattr(context, 'asset', None)
    if not asset or asset.id_type != 'ACTION' or asset.local_id or not asset.full_library_path:
        return None
    path = Path(asset.full_library_path)
    if not path.name.endswith('.asset.blend'):
        return None
    action = bpy.data.actions.get((asset.name, str(path)))
    if action is None:
        # Blender can store the link relative to the current file.
        action = next((item for item in bpy.data.actions if item.name == asset.name and item.library
                       and Path(bpy.path.abspath(item.library.filepath)).resolve() == path.resolve()), None)
    if action is None or capture.METADATA not in action:
        return None
    try:
        directory, _ = capture._shared_pose_library(context)
    except (OSError, ValueError):
        return None
    return (action, path) if path.resolve().parent == directory.resolve() else None


def _pixels(preview):
    buffers = {}
    for key in ('image', 'icon'):
        size = tuple(getattr(preview, key + '_size'))
        if min(size) <= 0:
            continue
        data = array('i', [0]) * (size[0] * size[1])
        getattr(preview, key + '_pixels').foreach_get(data)
        buffers[key] = (size, data)
    return buffers


def _stamp(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').digest()


def save_captured_preview(action, path):
    preview = action.preview
    if preview is None or not preview.is_image_custom or min(preview.image_size) <= 0:
        raise ValueError('Capture a screenshot for this pose first.')
    captured = _pixels(preview)
    # Always edit a fresh copy of the library Action. The linked one belongs
    # to the current character file and can have stale animation or metadata.
    original = _stamp(path)
    with TemporaryDirectory(prefix='.cd-preview-', dir=path.parent) as directory:
        staged = Path(directory) / 'preview.tmp'
        with bpy.types.BlendData.temp_data() as data:
            with data.libraries.load(str(path)) as (source, target):
                others = any(isinstance(getattr(source, key), list) and getattr(source, key)
                             for key in dir(source) if not key.startswith('_') and key != 'actions')
                if source.actions != [action.name] or others:
                    raise ValueError('Choose a single shared pose created by Save Pose.')
                target.actions = [action.name]
            saved, = target.actions
            if (saved.asset_data is None or not saved.get(capture.METADATA)
                    or saved[capture.METADATA] != action.get(capture.METADATA)):
                raise ValueError('The shared pose changed. Refresh the library and capture it again.')
            existing = saved.preview
            if existing and _pixels(existing) == captured:
                return
            new = saved.preview_ensure()
            for key, (size, pixels) in captured.items():
                setattr(new, 'is_' + key + '_custom', True)
                setattr(new, key + '_size', size)
                setattr(new, key + '_pixels', pixels)
            data.libraries.write(str(staged), {saved}, fake_user=True, compress=True)
        with bpy.types.BlendData.temp_data() as check:
            with check.libraries.load(str(staged), assets_only=True) as (source, target):
                if source.actions != [action.name]:
                    raise ValueError('The pose preview could not be written.')
                target.actions = [action.name]
            if _pixels(target.actions[0].preview) != captured:
                raise ValueError('The pose preview could not be verified.')
        if _stamp(path) != original:
            raise ValueError('The shared pose changed during capture. Refresh the library and try again.')
        os.replace(staged, path)


class CHARACTERDESIGNER_OT_save_captured_preview(bpy.types.Operator):
    bl_idname = 'character_designer.save_captured_preview'
    bl_label = 'Save Captured Preview'
    bl_description = "Save the captured screenshot in the shared pose's asset file"

    @classmethod
    def poll(cls, context):
        selected = _selected_capture(context)
        if selected:
            preview = selected[0].preview
            if preview and preview.is_image_custom and min(preview.image_size) > 0:
                return True
        cls.poll_message_set('Capture a screenshot for a shared Character Designer pose first.')
        return False

    def execute(self, context):
        selected = _selected_capture(context)
        if selected is None:
            return {'CANCELLED'}
        try:
            save_captured_preview(*selected)
        except (OSError, RuntimeError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if bpy.ops.asset.library_refresh.poll():
            bpy.ops.asset.library_refresh()
        self.report({'INFO'}, 'Saved the shared pose preview.')
        return {'FINISHED'}


def _draw(self, context):
    if _selected_capture(context):
        self.layout.operator(CHARACTERDESIGNER_OT_save_captured_preview.bl_idname)


def register():
    if not CHARACTERDESIGNER_OT_save_captured_preview.is_registered:
        bpy.utils.register_class(CHARACTERDESIGNER_OT_save_captured_preview)
        bpy.types.ASSETBROWSER_MT_metadata_preview_menu.append(_draw)


def unregister():
    if CHARACTERDESIGNER_OT_save_captured_preview.is_registered:
        bpy.types.ASSETBROWSER_MT_metadata_preview_menu.remove(_draw)
        bpy.utils.unregister_class(CHARACTERDESIGNER_OT_save_captured_preview)

