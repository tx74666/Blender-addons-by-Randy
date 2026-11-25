"""Saved, workspace-specific views over the animation lists."""

from hashlib import sha256

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from . import animation_worklist_edit_scene as edit_scene


_STORAGE = 'character_designer_motion_pins_v1'


def pin_owner(scene):
    return edit_scene.editing_scene(scene) or scene


def row_key(row):
    key = getattr(row, 'clip_key', '') or getattr(row, 'item_id', '')
    # Composition can include a motion belonging to another workspace.
    source = getattr(row, 'workspace_identity', '')
    state = getattr(getattr(row, 'id_data', None), 'character_designer_animation_worklist', None)
    if key and state and source and source != state.workspace_identity:
        return source + '\n' + key
    return key


def _hash(value):
    # Blender ID property names have a fixed byte limit, whereas clip identifiers do not.
    return sha256(value.encode('utf8')).hexdigest()[:48]


def _view(scene, *, create=False):
    owner = pin_owner(scene)
    state = getattr(owner, 'character_designer_animation_worklist', None)
    identity = getattr(state, 'workspace_identity', '')
    if not identity:
        if create:
            raise ValueError('Connect this animation workspace first.')
        return {}
    scope = _hash(identity)
    store = owner.get(_STORAGE)
    if create:
        if owner.library or owner.override_library:
            raise ValueError('Open a local editing scene to change its motion list.')
        if store is None:
            owner[_STORAGE] = {}
            store = owner[_STORAGE]
        if scope not in store:
            store[scope] = {}
    return store.get(scope, {}) if store is not None else {}


def pinned_only(scene):
    return bool(_view(scene).get('pinned_only', False))


def is_pinned(scene, row):
    key = row_key(row)
    return bool(key and _view(scene).get(_hash(key)) == key)


def filter_rows(ui_list, context, data, propname):
    view = _view(context.scene)
    only = view.get('pinned_only', False)
    terms = ui_list.filter_name.casefold().split()
    if not only and not terms:
        return [], []
    flags = []
    for row in getattr(data, propname):
        key = row_key(row)
        eligible = not only or (key and view.get(_hash(key)) == key)
        visible = eligible and all(term in row.name.casefold() for term in terms)
        flags.append(ui_list.bitflag_filter_item if visible else 0)
    return flags, []


def draw_pin(layout, scene, row):
    key = row_key(row)
    if key:
        layout.operator('character_designer.motion_toggle_pin', text='', emboss=False,
                        icon='PINNED' if is_pinned(scene, row) else 'UNPINNED').key = key


def redraw_lists(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D', 'DOPESHEET_EDITOR'}:
                area.tag_redraw()


class CHARACTERDESIGNER_OT_motion_toggle_pin(Operator):
    bl_idname = 'character_designer.motion_toggle_pin'
    bl_label = 'Pin / Unpin Motion'
    bl_description = 'Include this motion in the pinned list or remove it'
    bl_options = {'UNDO'}

    key: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        state = context.scene.character_designer_animation_worklist
        rows = state.items if state.items else state.catalog
        if not self.key or not any(row_key(row) == self.key for row in rows):
            self.report({'ERROR'}, 'The motion is no longer in this list.')
            return {'CANCELLED'}
        try:
            view = _view(context.scene, create=True)
            prop = _hash(self.key)
            if view.get(prop) == self.key:
                del view[prop]
            else:
                view[prop] = self.key
        except (ValueError, RuntimeError, TypeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        redraw_lists(context)
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_motion_set_view(Operator):
    bl_idname = 'character_designer.motion_set_view'
    bl_label = 'Show Motion List'
    bl_description = 'Switch between all motions and your pinned motions'
    bl_options = {'UNDO'}

    pinned_only: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            if pinned_only(context.scene) != self.pinned_only:
                _view(context.scene, create=True)['pinned_only'] = self.pinned_only
        except (ValueError, RuntimeError, TypeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        redraw_lists(context)
        return {'FINISHED'}


CLASSES = (CHARACTERDESIGNER_OT_motion_toggle_pin, CHARACTERDESIGNER_OT_motion_set_view)
