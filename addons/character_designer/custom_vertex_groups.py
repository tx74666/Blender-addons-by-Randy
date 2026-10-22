"""Saved modeling selections using Blender's native attribute catalog.

The ``Custom_`` prefix is reserved for these groups: existing POINT/INT
attributes with that prefix are also listed as groups. New groups contain
0/1 membership; deform vertex groups and painted weights are never written.
Native BMesh custom data owns catalog, membership and Edit Mode Undo together.
"""
import bmesh
import bpy
import hashlib
import json
from array import array
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator, PropertyGroup, UIList

from . import custom_vertex_visibility as visibility
from . import mesh_selection_rows as row_selection


PREFIX = 'Custom_'
LOCK_PREFIX = '.cdvg_lock_'
LABEL_PROPERTY = 'name'
VIEW_PROPERTY = 'character_designer_custom_vertex_group_view'
VIEW_INDEX_PROPERTY = 'character_designer_custom_vertex_group_view_index'
MODE_PROPERTY = 'character_designer_custom_vertex_group_mode'
_VIEW_SYNC_DEPTH = 0
_VIEW_DESCRIPTIONS = {VIEW_PROPERTY: 'Character Designer transient custom group rows',
                      VIEW_INDEX_PROPERTY: 'Character Designer transient active custom group row',
                      MODE_PROPERTY: 'Character Designer mesh selection presentation mode'}


def _mesh(context, *, edit=False):
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        raise ValueError('Select a mesh.')
    mesh = obj.data
    if (obj.library or mesh.library or obj.override_library or mesh.override_library
            or not obj.is_editable or not mesh.is_editable):
        raise ValueError('Use a local editable mesh.')
    if mesh.users != 1:
        raise ValueError('Use a mesh with single-user data.')
    if obj.mode not in {'OBJECT', 'EDIT'} or (edit and obj.mode != 'EDIT'):
        raise ValueError('Use Mesh Edit Mode.' if edit else 'Use Object or Mesh Edit Mode.')
    if obj.mode == 'EDIT' and len(context.objects_in_mode) != 1:
        raise ValueError('Edit one mesh at a time.')
    return mesh


def _is_group(attribute):
    return (attribute is not None and attribute.domain == 'POINT' and attribute.data_type == 'INT'
            and attribute.name.startswith(PREFIX) and len(attribute.name) > len(PREFIX))


def group_attributes(mesh):
    return [attribute for attribute in mesh.attributes if _is_group(attribute)]


def _active(mesh):
    attribute = mesh.attributes.active
    if not _is_group(attribute):
        raise ValueError('Select a custom vertex group.')
    return attribute


def _layer(bm, attribute):
    layer = bm.verts.layers.int.get(attribute.name)
    if layer is None:
        raise ValueError('The custom group attribute is missing.')
    return layer


def _lock_name(name):
    return LOCK_PREFIX + hashlib.sha256(name.encode('utf-8')).hexdigest()[:20]


def _marker(mesh, name):
    marker = mesh.attributes.get(_lock_name(name))
    if marker is not None and (marker.domain != 'POINT' or marker.data_type != 'INT'):
        raise ValueError('The custom group lock metadata has an incompatible type.')
    return marker


def is_locked(attribute):
    if not _is_group(attribute):
        raise ValueError('Select a custom vertex group.')
    return _marker(attribute.id_data, attribute.name) is not None


def _activate(mesh, name):
    index = mesh.attributes.find(name)
    if index >= 0 and mesh.attributes.active_index != index:
        mesh.attributes.active_index = index


def _remove_native(mesh, name, bm=None):
    attribute = mesh.attributes.get(name)
    if attribute is None:
        raise ValueError('The custom group attribute is missing.')
    if not _is_group(attribute) and name not in {_lock_name(item.name) for item in group_attributes(mesh)}:
        raise ValueError('Only a custom group or its lock can be removed.')
    if bm is not None:
        bm.verts.layers.int.remove(_layer(bm, attribute))
    else:
        mesh.attributes.remove(attribute)


def set_locked(context, group_name, locked):
    mesh = _mesh(context)
    attribute = mesh.attributes.get(group_name)
    if not _is_group(attribute):
        raise ValueError('Select a custom vertex group.')
    marker = _marker(mesh, group_name)
    if (marker is not None) == bool(locked):
        return
    active_name = mesh.attributes.active.name if mesh.attributes.active else ''
    bm = bmesh.from_edit_mesh(mesh) if mesh.is_editmode else None
    if locked:
        name = _lock_name(group_name)
        if bm is not None:
            bm.verts.layers.int.new(name)
        else:
            mesh.attributes.new(name, 'INT', 'POINT')
    else:
        _remove_native(mesh, marker.name, bm)
    if bm is not None:
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    _activate(mesh, active_name)


def _new_name(mesh, label, bm=None, *, exclude=()):
    taken = set(mesh.attributes.keys())
    if bm is not None:
        for domain in (bm.verts, bm.edges, bm.faces, bm.loops):
            for kind in ('int', 'float', 'float_vector', 'string', 'bool',
                         'color', 'float_color', 'byte_color', 'uv', 'shape'):
                layers = getattr(domain.layers, kind, None)
                if layers is not None:
                    taken.update(layers.keys())
    taken.difference_update(exclude)
    for number in range(len(taken) + 1):
        suffix = '' if number == 0 else f'.{number:03d}'
        stem = (PREFIX + label).encode('utf-8')[:63 - len(suffix)].decode('utf-8', errors='ignore')
        name = stem + suffix
        if name not in taken:
            return name
    raise ValueError('Could not find an unused custom group name.')


def new_group(context, name='Group'):
    mesh = _mesh(context)
    label = name.strip()
    if not label:
        raise ValueError('Enter a group name.')
    bm = bmesh.from_edit_mesh(mesh) if mesh.is_editmode else None
    attribute_name = _new_name(mesh, label, bm)
    if bm is not None:
        layer = bm.verts.layers.int.new(attribute_name)
        attribute_name = layer.name
        for vertex in bm.verts:
            vertex[layer] = 0
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    else:
        attribute_name = mesh.attributes.new(attribute_name, 'INT', 'POINT').name
    mesh.attributes.active_index = mesh.attributes.find(attribute_name)
    return attribute_name


def delete_group(context):
    mesh = _mesh(context)
    attribute = _active(mesh)
    names = [item.name for item in group_attributes(mesh)]
    name = attribute.name
    index = names.index(name)
    marker = _marker(mesh, name)
    marker_name = marker.name if marker is not None else None
    visibility.before_delete(context, name)
    bm = bmesh.from_edit_mesh(mesh) if mesh.is_editmode else None
    if marker_name is not None:
        _remove_native(mesh, marker_name, bm)
    _remove_native(mesh, name, bm)
    if bm is not None:
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    remaining = group_attributes(mesh)
    if remaining:
        mesh.attributes.active_index = mesh.attributes.find(remaining[min(index, len(remaining) - 1)].name)


def edit_membership(context, member):
    mesh = _mesh(context, edit=True)
    attribute = _active(mesh)
    if is_locked(attribute):
        raise ValueError('Unlock this custom group before changing its vertices.')
    visibility.preflight(context)
    bm = bmesh.from_edit_mesh(mesh)
    layer = _layer(bm, attribute)
    for vertex in bm.verts:
        if vertex.select and not vertex.hide:
            vertex[layer] = 1 if member else 0
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    visibility.refresh_custom(context)


def select_group(context, *, extend=False):
    mesh = _mesh(context, edit=True)
    bm = bmesh.from_edit_mesh(mesh)
    layer = _layer(bm, _active(mesh))
    context.tool_settings.mesh_select_mode = (True, False, False)
    bm.select_mode = {'VERT'}
    if not extend:
        for domain in (bm.faces, bm.edges, bm.verts):
            for element in domain:
                element.select_set(False)
    for vertex in bm.verts:
        if not vertex.hide and vertex[layer] != 0:
            vertex.select_set(True)
    if not extend:
        bm.select_history.clear()
    else:
        bm.select_history.validate()
    bm.select_flush(True)
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)


def deselect_group(context):
    mesh = _mesh(context, edit=True)
    bm = bmesh.from_edit_mesh(mesh)
    layer = _layer(bm, _active(mesh))
    for vertex in bm.verts:
        if not vertex.hide and vertex[layer] != 0:
            vertex.select_set(False)
    bm.select_flush(False)
    bm.select_history.validate()
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)


def _values(mesh, name, bm=None):
    if not _is_group(mesh.attributes.get(name)):
        raise ValueError('Select a custom vertex group.')
    if bm is not None:
        layer = _layer(bm, mesh.attributes[name])
        return tuple(vertex[layer] for vertex in bm.verts)
    values = array('i', [0]) * len(mesh.vertices)
    mesh.attributes[name].data.foreach_get('value', values)
    return values


def _write_values(mesh, name, values, bm=None):
    if not _is_group(mesh.attributes.get(name)):
        raise ValueError('Select a custom vertex group.')
    if bm is not None:
        layer = _layer(bm, mesh.attributes[name])
        for vertex, value in zip(bm.verts, values):
            vertex[layer] = value
    else:
        mesh.attributes[name].data.foreach_set('value', values)


def _swap_groups(mesh, first, second, bm=None):
    if not all(_is_group(mesh.attributes.get(name)) for name in (first, second)):
        raise ValueError('Move only custom vertex groups.')
    first_values, second_values = _values(mesh, first, bm), _values(mesh, second, bm)
    temporary = '.cdvg_move'
    number = 0
    while temporary in mesh.attributes:
        number += 1
        temporary = f'.cdvg_move.{number:03d}'
    # Only these two native layers change. Swapping their names AND payloads
    # moves group order without changing either named group's membership.
    mesh.attributes[first].name = temporary
    mesh.attributes[second].name = first
    mesh.attributes[temporary].name = second
    _write_values(mesh, second, second_values, bm)
    _write_values(mesh, first, first_values, bm)


def move_group(context, direction):
    mesh = _mesh(context)
    if direction not in {'UP', 'DOWN'}:
        raise ValueError('Choose Up or Down.')
    name = _active(mesh).name
    names = [item.name for item in group_attributes(mesh)]
    index = names.index(name)
    other = index + (-1 if direction == 'UP' else 1)
    if 0 <= other < len(names):
        bm = bmesh.from_edit_mesh(mesh) if mesh.is_editmode else None
        _swap_groups(mesh, name, names[other], bm)
        if bm is not None:
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        _activate(mesh, name)


def group_action(context, action):
    mesh = _mesh(context, edit=action == 'CLEAR')
    names = [item.name for item in group_attributes(mesh)]
    active = mesh.attributes.active.name if _is_group(mesh.attributes.active) else ''
    bm = bmesh.from_edit_mesh(mesh) if mesh.is_editmode else None
    if action == 'SORT_NAME':
        desired = sorted(names, key=lambda name: (name.casefold(), name))
        for target, name in enumerate(desired):
            index = names.index(name)
            while index > target:
                _swap_groups(mesh, names[index - 1], names[index], bm)
                names[index - 1], names[index] = names[index], names[index - 1]
                index -= 1
    elif action == 'DUPLICATE':
        source = _active(mesh)
        name, locked = source.name, is_locked(source)
        values = _values(mesh, name, bm)
        duplicate = new_group(context, name[len(PREFIX):])
        _write_values(mesh, duplicate, values, bm)
        if locked:
            set_locked(context, duplicate, True)
        active = duplicate
    elif action == 'CLEAR':
        attribute = _active(mesh)
        if is_locked(attribute):
            raise ValueError('Unlock this custom group before clearing its vertices.')
        visibility.preflight(context)
        _write_values(mesh, attribute.name, [0] * len(bm.verts), bm)
    elif action in {'LOCK_ALL', 'UNLOCK_ALL', 'INVERT_LOCKS'}:
        states = [(name, is_locked(mesh.attributes[name])) for name in names]
        for name, locked in states:
            set_locked(context, name, action == 'LOCK_ALL' or (action == 'INVERT_LOCKS' and not locked))
    elif action in {'DELETE_ALL', 'DELETE_UNLOCKED', 'DELETE_EMPTY_UNLOCKED'}:
        targets = [name for name in names if action == 'DELETE_ALL'
                   or (not is_locked(mesh.attributes[name])
                       and (action == 'DELETE_UNLOCKED' or not any(_values(mesh, name, bm))))]
        for name in targets:
            _marker(mesh, name)
        for name in targets:
            _activate(mesh, name)
            delete_group(context)
    else:
        raise ValueError('Unknown custom group action.')
    if bm is not None:
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        if action == 'CLEAR':
            visibility.refresh_custom(context)
    _activate(mesh, active)


def _get_label(attribute):
    return attribute.name[len(PREFIX):] if _is_group(attribute) else attribute.name


def _set_label(attribute, value):
    mesh = _mesh(bpy.context)
    if attribute.id_data != mesh or not _is_group(attribute):
        raise ValueError('Rename a custom group on the active mesh.')
    label = value.strip()
    if not label:
        raise ValueError('Enter a group name.')
    old_name = attribute.name
    bm = bmesh.from_edit_mesh(mesh) if mesh.is_editmode else None
    name = _new_name(mesh, label, bm, exclude={old_name})
    marker = _marker(mesh, old_name)
    marker_name = _lock_name(name)
    if marker is not None and marker.name != marker_name and marker_name in mesh.attributes:
        raise ValueError('The renamed group conflicts with existing lock metadata.')
    visibility.preflight_rename(mesh, old_name, name)
    old_marker_name = marker.name if marker is not None else None
    try:
        attribute.name = name
        if attribute.name != name:
            raise ValueError('The custom group could not be renamed.')
        if marker is not None:
            marker.name = marker_name
            if marker.name != marker_name:
                raise ValueError('The custom group lock could not be renamed.')
        visibility.rename_state(mesh, old_name, name)
    except Exception:
        attribute.name = old_name
        if marker is not None:
            marker.name = old_marker_name
        raise
    row_selection.rename_cache(bpy.context.window_manager, VIEW_PROPERTY, mesh, old_name, name)


def _mesh_token(mesh):
    return str(getattr(mesh, 'session_uid', None) or mesh.as_pointer())


def _view_attribute(row, context=None):
    mesh = _mesh(context or bpy.context)
    if row.mesh_token != _mesh_token(mesh):
        raise ValueError('The custom group view belongs to another mesh.')
    attribute = mesh.attributes.get(row.native_name)
    if not _is_group(attribute):
        raise ValueError('The custom group view is out of date.')
    return attribute


def _view_label(row):
    try:
        return _get_label(_view_attribute(row))
    except (ValueError, ReferenceError):
        return ''


def _view_rename(row, value):
    attribute = _view_attribute(row)
    mesh = attribute.id_data
    label = value.strip()
    if not label:
        raise ValueError('Enter a group name.')
    bm = bmesh.from_edit_mesh(mesh) if mesh.is_editmode else None
    name = _new_name(mesh, label, bm, exclude={row.native_name})
    result = bpy.ops.character_designer.custom_vertex_group_rename(
        'EXEC_DEFAULT', True, group_name=row.native_name, name=label)
    if result != {'FINISHED'}:
        raise ValueError('The custom group could not be renamed.')
    row.native_name = name


def _view_active(window_manager, context):
    if _VIEW_SYNC_DEPTH:
        return
    rows = getattr(window_manager, VIEW_PROPERTY)
    index = getattr(window_manager, VIEW_INDEX_PROPERTY)
    if 0 <= index < len(rows):
        try:
            attribute = _view_attribute(rows[index], context)
            row_selection.choose(rows, attribute.id_data, PREFIX, attribute.name)
            _activate(attribute.id_data, attribute.name)
        except (ValueError, ReferenceError):
            pass


def activate_view(context):
    """Restore this catalog's remembered active row on an explicit tab change."""
    window_manager = context.window_manager
    rows = getattr(window_manager, VIEW_PROPERTY)
    index = getattr(window_manager, VIEW_INDEX_PROPERTY)
    if 0 <= index < len(rows):
        try:
            attribute = _view_attribute(rows[index], context)
            _activate(attribute.id_data, attribute.name)
            return True
        except (ValueError, ReferenceError):
            pass
    return False


def _mode_changed(window_manager, context):
    if getattr(window_manager, MODE_PROPERTY) == 'PARTS':
        from . import custom_vertex_parts
        custom_vertex_parts.activate_view(context)
    else:
        activate_view(context)


def _sync_view(context):
    global _VIEW_SYNC_DEPTH
    mesh = _mesh(context)
    rows = getattr(context.window_manager, VIEW_PROPERTY)
    names = [item.name for item in group_attributes(mesh)]
    token = _mesh_token(mesh)
    previous_index = getattr(context.window_manager, VIEW_INDEX_PROPERTY)
    remembered = ''
    if 0 <= previous_index < len(rows):
        previous = rows[previous_index]
        if previous.mesh_token == token and previous.native_name in names:
            remembered = previous.native_name
    active = mesh.attributes.active.name if _is_group(mesh.attributes.active) else ''
    active = active or remembered
    selected = row_selection.snapshot(rows, mesh, names, active)
    anchor = row_selection.anchor_name(rows, mesh, names)
    index = names.index(active) if active in selected else -1
    # Cache synchronization is read-only with respect to native active state.
    _VIEW_SYNC_DEPTH += 1
    try:
        while len(rows) > len(names):
            rows.remove(len(rows) - 1)
        while len(rows) < len(names):
            rows.add()
        for row, name in zip(rows, names):
            if row.native_name != name:
                row.native_name = name
            if row.mesh_token != token:
                row.mesh_token = token
            if row.selected != (name in selected):
                row.selected = name in selected
            row.anchor = name == anchor
        if getattr(context.window_manager, VIEW_INDEX_PROPERTY) != index:
            setattr(context.window_manager, VIEW_INDEX_PROPERTY, index)
    finally:
        _VIEW_SYNC_DEPTH -= 1
    return rows


def sync_view(context):
    """Discardable UI rows; native attributes remain the only group catalog."""
    return _sync_view(context)


def selected_rows(context):
    mesh = _mesh(context)
    return row_selection.chosen_names(getattr(context.window_manager, VIEW_PROPERTY), mesh, PREFIX)


def pick_row(context, name, *, extend=False, range_select=False, visible_names=None):
    global _VIEW_SYNC_DEPTH
    mesh = _mesh(context)
    rows = _sync_view(context)
    _VIEW_SYNC_DEPTH += 1
    try:
        index = row_selection.choose(rows, mesh, PREFIX, name, extend,
                                     range_select=range_select, visible_names=visible_names)
        setattr(context.window_manager, VIEW_INDEX_PROPERTY, index if rows[index].selected else -1)
    finally:
        _VIEW_SYNC_DEPTH -= 1
    _activate(mesh, name)


class CHARACTERDESIGNER_PG_custom_vertex_group_view(PropertyGroup):
    native_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    mesh_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    selected: BoolProperty(default=False, options={'SKIP_SAVE'})
    anchor: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    name: StringProperty(name='Name', get=_view_label, set=_view_rename, options={'SKIP_SAVE'})


class CHARACTERDESIGNER_UL_custom_vertex_groups(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        try:
            attribute = _view_attribute(item, context)
        except (ValueError, ReferenceError):
            layout.label(text='')
            return
        row_selection.draw_row_pick(layout, 'character_designer.custom_vertex_group_pick',
            'group_name', attribute.name, _get_label(attribute), 'GROUP_VERTEX', item.selected,
            row_selection.visible_names(self, context, data, VIEW_PROPERTY))
        try:
            locked = is_locked(attribute)
        except ValueError:
            locked = True
        controls = layout.row(align=True)
        controls.alignment = 'LEFT'
        controls.operator('character_designer.custom_vertex_group_lock', text='', emboss=False,
                        icon='LOCKED' if locked else 'UNLOCKED').group_name = attribute.name

    def filter_items(self, context, data, property):
        attributes = getattr(data, property)
        flags = bpy.types.UI_UL_list.filter_items_by_name(self.filter_name, self.bitflag_filter_item,
            attributes, 'name', reverse=self.use_filter_invert) if self.filter_name else []
        if not flags:
            flags = [self.bitflag_filter_item] * len(attributes)
        for index, item in enumerate(attributes):
            try:
                _view_attribute(item, context)
            except (ValueError, ReferenceError):
                flags[index] = self.bitflag_item_never_show
        order = bpy.types.UI_UL_list.sort_items_by_name(attributes, 'name') if self.use_filter_sort_alpha else []
        return flags, order


class _GroupOperator:
    @classmethod
    def poll(cls, context):
        try:
            _mesh(context, edit=cls.edit_only)
            return True
        except (ValueError, ReferenceError):
            return False

    def execute(self, context):
        try:
            self.apply(context)
            return {'FINISHED'}
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_custom_vertex_group_new(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_new'
    bl_label = 'New Custom Vertex Group'
    bl_description = 'Create an empty modeling selection group'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = False
    name: StringProperty(name='Name', default='Group')

    def apply(self, context):
        name = new_group(context, self.name)
        row_selection.new_row(context.window_manager, VIEW_PROPERTY, VIEW_INDEX_PROPERTY,
                              _mesh(context), name, PREFIX)


class CHARACTERDESIGNER_OT_custom_vertex_group_pick(_GroupOperator, row_selection.RowRenameClick, Operator):
    bl_idname = 'character_designer.custom_vertex_group_pick'
    bl_label = 'Select Group Row'
    bl_description = 'Choose this group; Shift selects a range, Ctrl toggles one, double-click renames'
    bl_options = {'INTERNAL'}
    edit_only = False
    group_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    extend: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    range_select: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    visible_names: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        self.extend, self.range_select = event.ctrl, event.shift
        result = self.execute(context)
        return self.wait_for_double_click(context, event, _mesh(context)) if result == {'FINISHED'} else result

    def rename_row(self, context):
        if getattr(context.window_manager, MODE_PROPERTY) != 'PARTS':
            bpy.ops.character_designer.custom_vertex_group_rename('INVOKE_DEFAULT', group_name=self.group_name)
            return True
        return False

    def apply(self, context):
        pick_row(context, self.group_name, extend=self.extend, range_select=self.range_select,
                 visible_names=json.loads(self.visible_names) if self.visible_names else None)


class CHARACTERDESIGNER_OT_custom_vertex_group_delete(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_delete'
    bl_label = 'Delete Custom Vertex Group'
    bl_description = 'Delete this modeling selection group'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = False

    def apply(self, context):
        delete_group(context)


class CHARACTERDESIGNER_OT_custom_vertex_group_assign(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_assign'
    bl_label = 'Assign'
    bl_description = 'Add selected vertices to this custom group'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = True

    def apply(self, context):
        edit_membership(context, True)


class CHARACTERDESIGNER_OT_custom_vertex_group_remove(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_remove'
    bl_label = 'Remove'
    bl_description = 'Remove selected vertices from this custom group'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = True

    def apply(self, context):
        edit_membership(context, False)


class CHARACTERDESIGNER_OT_custom_vertex_group_select(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_select'
    bl_label = 'Select'
    bl_description = 'Select visible vertices from selected rows; Shift-click to add to the current vertices'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = True
    group_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    extend: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    use_rows: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def apply(self, context):
        if self.use_rows:
            mesh = _mesh(context, edit=True)
            row_selection.select_vertices(context, mesh, selected_rows(context), PREFIX,
                                          select=True, extend=self.extend)
            return
        if self.group_name:
            mesh = _mesh(context, edit=True)
            if not _is_group(mesh.attributes.get(self.group_name)):
                raise ValueError('Select a custom vertex group.')
            _activate(mesh, self.group_name)
        select_group(context, extend=self.extend)


class CHARACTERDESIGNER_OT_custom_vertex_part_new(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_part_new'
    bl_label = 'New Part from Selection'
    bl_description = 'Save selected vertices in the independent Parts list'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = True

    def apply(self, context):
        from . import custom_vertex_parts
        custom_vertex_parts.new_part(context)


class CHARACTERDESIGNER_OT_custom_vertex_group_visibility(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_visibility'
    bl_label = 'Part Visibility'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = True
    group_name: StringProperty(options={'HIDDEN'})
    action: EnumProperty(items=[('HIDE', 'Toggle Visibility', ''), ('SOLO', 'Toggle Solo', ''),
                                ('RESET', 'Restore Visibility', '')])

    @classmethod
    def description(cls, context, properties):
        return {'HIDE': 'Hide or show this group; overlapping hidden groups can keep shared vertices hidden',
                'SOLO': 'Show only starred groups together; preserve manual hides and the eye layout',
                'RESET': 'Clear eyes and stars, restoring the previous manual visibility'}[properties.action]

    def apply(self, context):
        if self.action == 'RESET':
            visibility.reset_legacy_visibility(context)
        else:
            mesh = _mesh(context, edit=True)
            if not _is_group(mesh.attributes.get(self.group_name)):
                raise ValueError('Select a custom vertex group.')
            if self.action == 'SOLO':
                visibility.toggle_solo(context, self.group_name)
            else:
                visibility.toggle_hidden(context, self.group_name)


class CHARACTERDESIGNER_OT_custom_vertex_group_deselect(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_deselect'
    bl_label = 'Deselect'
    bl_description = 'Deselect visible vertices from the selected group rows'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = True
    use_rows: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def apply(self, context):
        if self.use_rows:
            mesh = _mesh(context, edit=True)
            row_selection.select_vertices(context, mesh, selected_rows(context), PREFIX, select=False)
        else:
            deselect_group(context)


class CHARACTERDESIGNER_OT_custom_vertex_group_rename(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_rename'
    bl_label = 'Rename Custom Group'
    bl_description = 'Rename this modeling selection group'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = False
    group_name: StringProperty(options={'HIDDEN'})
    name: StringProperty(name='Name')
    mesh_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        try:
            mesh = _mesh(context)
            attribute = mesh.attributes.get(self.group_name)
            if not _is_group(attribute):
                raise ValueError('Select a custom vertex group.')
            self.name = _get_label(attribute)
            self.mesh_token = _mesh_token(mesh)
        except (ValueError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.activate_init = True
        self.layout.prop(self, 'name')

    def apply(self, context):
        mesh = _mesh(context)
        if self.mesh_token and self.mesh_token != _mesh_token(mesh):
            raise ValueError('The rename field belongs to another mesh.')
        attribute = mesh.attributes.get(self.group_name)
        if not _is_group(attribute):
            raise ValueError('Select a custom vertex group.')
        _set_label(attribute, self.name)


class CHARACTERDESIGNER_OT_custom_vertex_group_lock(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_lock'
    bl_label = 'Toggle Custom Group Lock'
    bl_description = 'Protect this group from Assign, Remove and Clear'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = False
    group_name: StringProperty(options={'HIDDEN'})

    def apply(self, context):
        mesh = _mesh(context)
        attribute = mesh.attributes.get(self.group_name)
        if not _is_group(attribute):
            raise ValueError('Select a custom vertex group.')
        set_locked(context, self.group_name, not is_locked(attribute))


class CHARACTERDESIGNER_OT_custom_vertex_group_move(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_move'
    bl_label = 'Move Custom Group'
    bl_description = 'Move this group in the saved list order'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = False
    direction: EnumProperty(items=[('UP', 'Up', ''), ('DOWN', 'Down', '')])

    def apply(self, context):
        move_group(context, self.direction)


class CHARACTERDESIGNER_OT_custom_vertex_group_action(_GroupOperator, Operator):
    bl_idname = 'character_designer.custom_vertex_group_action'
    bl_label = 'Custom Vertex Groups'
    bl_description = 'Manage modeling selection groups'
    bl_options = {'REGISTER', 'UNDO'}
    edit_only = False
    action: EnumProperty(items=[(key, label, '') for key, label in (
        ('SORT_NAME', 'Sort by Name'), ('DUPLICATE', 'Duplicate Group'), ('CLEAR', 'Clear Group'),
        ('DELETE_EMPTY_UNLOCKED', 'Delete Empty Unlocked Groups'),
        ('DELETE_UNLOCKED', 'Delete All Unlocked Groups'), ('DELETE_ALL', 'Delete All Groups'),
        ('LOCK_ALL', 'Lock All'), ('UNLOCK_ALL', 'Unlock All'), ('INVERT_LOCKS', 'Invert Locks'))])

    def apply(self, context):
        group_action(context, self.action)


class CHARACTERDESIGNER_MT_custom_vertex_groups(Menu):
    bl_label = 'Custom Vertex Groups'

    def draw(self, context):
        layout = self.layout
        for key, label in (('SORT_NAME', 'Sort by Name'), ('DUPLICATE', 'Duplicate Group')):
            layout.operator('character_designer.custom_vertex_group_action', text=label).action = key
        clear = layout.row()
        clear.enabled = context.mode == 'EDIT_MESH'
        clear.operator('character_designer.custom_vertex_group_action', text='Clear Group').action = 'CLEAR'
        layout.separator()
        for key, label in (('DELETE_EMPTY_UNLOCKED', 'Delete Empty Unlocked Groups'),
                           ('DELETE_UNLOCKED', 'Delete All Unlocked Groups'), ('DELETE_ALL', 'Delete All Groups')):
            layout.operator('character_designer.custom_vertex_group_action', text=label).action = key
        layout.separator()
        for key, label in (('LOCK_ALL', 'Lock All'), ('UNLOCK_ALL', 'Unlock All'), ('INVERT_LOCKS', 'Invert Locks')):
            layout.operator('character_designer.custom_vertex_group_action', text=label).action = key


def draw(layout, context):
    try:
        mesh = _mesh(context)
    except (ValueError, ReferenceError) as exc:
        layout.label(text=str(exc), icon='INFO')
        return
    layout.row(align=True).prop(context.window_manager, MODE_PROPERTY, expand=True)
    if getattr(context.window_manager, MODE_PROPERTY) == 'PARTS':
        from . import custom_vertex_parts
        custom_vertex_parts.draw(layout, context)
        return
    _sync_view(context)
    row = layout.row()
    row.template_list('CHARACTERDESIGNER_UL_custom_vertex_groups', 'Groups', context.window_manager, VIEW_PROPERTY,
                      context.window_manager, VIEW_INDEX_PROPERTY, rows=3)
    column = row.column(align=True)
    column.operator('character_designer.custom_vertex_group_new', text='', icon='ADD').name = 'Group'
    remove = column.column(align=True)
    remove.enabled = _is_group(mesh.attributes.active)
    remove.operator('character_designer.custom_vertex_group_delete', text='', icon='REMOVE')
    column.menu('CHARACTERDESIGNER_MT_custom_vertex_groups', text='', icon='DOWNARROW_HLT')
    names = [item.name for item in group_attributes(mesh)]
    active = mesh.attributes.active.name if _is_group(mesh.attributes.active) else ''
    index = names.index(active) if active else -1
    for direction, icon, enabled in (('UP', 'TRIA_UP', index > 0),
                                     ('DOWN', 'TRIA_DOWN', 0 <= index < len(names) - 1)):
        move = column.row(align=True)
        move.enabled = enabled
        move.operator('character_designer.custom_vertex_group_move', text='', icon=icon).direction = direction
    row = layout.row(align=True)
    row.enabled = mesh.is_editmode
    membership = row.row(align=True)
    membership.enabled = bool(active)
    if active:
        try:
            membership.enabled = not is_locked(mesh.attributes.active)
        except ValueError:
            membership.enabled = False
    membership.operator('character_designer.custom_vertex_group_assign')
    membership.operator('character_designer.custom_vertex_group_remove')
    selection = row.row(align=True)
    selection.enabled = bool(selected_rows(context))
    op = selection.operator('character_designer.custom_vertex_group_select')
    op.group_name, op.use_rows = '', True
    selection.operator('character_designer.custom_vertex_group_deselect').use_rows = True


CLASSES = (CHARACTERDESIGNER_PG_custom_vertex_group_view, CHARACTERDESIGNER_UL_custom_vertex_groups,
           CHARACTERDESIGNER_OT_custom_vertex_group_new, CHARACTERDESIGNER_OT_custom_vertex_group_delete,
           CHARACTERDESIGNER_OT_custom_vertex_group_pick,
           CHARACTERDESIGNER_OT_custom_vertex_group_assign, CHARACTERDESIGNER_OT_custom_vertex_group_remove,
           CHARACTERDESIGNER_OT_custom_vertex_group_select, CHARACTERDESIGNER_OT_custom_vertex_group_deselect,
           CHARACTERDESIGNER_OT_custom_vertex_part_new, CHARACTERDESIGNER_OT_custom_vertex_group_visibility,
           CHARACTERDESIGNER_OT_custom_vertex_group_rename,
           CHARACTERDESIGNER_OT_custom_vertex_group_lock, CHARACTERDESIGNER_OT_custom_vertex_group_move,
           CHARACTERDESIGNER_OT_custom_vertex_group_action, CHARACTERDESIGNER_MT_custom_vertex_groups)


def register_properties():
    for name in _VIEW_DESCRIPTIONS:
        prop = bpy.types.WindowManager.bl_rna.properties.get(name)
        if prop is not None and not _owned_view_property(name, prop):
            raise RuntimeError('A WindowManager property conflicts with Custom Vertex Groups.')
    added = []
    try:
        if not hasattr(bpy.types.WindowManager, VIEW_PROPERTY):
            setattr(bpy.types.WindowManager, VIEW_PROPERTY, CollectionProperty(
                type=CHARACTERDESIGNER_PG_custom_vertex_group_view,
                description=_VIEW_DESCRIPTIONS[VIEW_PROPERTY], options={'SKIP_SAVE'}))
            added.append(VIEW_PROPERTY)
        if not hasattr(bpy.types.WindowManager, VIEW_INDEX_PROPERTY):
            setattr(bpy.types.WindowManager, VIEW_INDEX_PROPERTY, IntProperty(default=-1, min=-1,
                description=_VIEW_DESCRIPTIONS[VIEW_INDEX_PROPERTY], options={'SKIP_SAVE'}, update=_view_active))
            added.append(VIEW_INDEX_PROPERTY)
        if not hasattr(bpy.types.WindowManager, MODE_PROPERTY):
            setattr(bpy.types.WindowManager, MODE_PROPERTY, EnumProperty(
                items=[('GROUPS', 'Custom Vertex', 'Edit the saved vertex sets and their membership'),
                       ('PARTS', 'Parts', 'Manage independent mesh parts, visibility and Solo')],
                default='GROUPS', description=_VIEW_DESCRIPTIONS[MODE_PROPERTY], options={'SKIP_SAVE'}, update=_mode_changed))
            added.append(MODE_PROPERTY)
    except Exception:
        for name in reversed(added):
            delattr(bpy.types.WindowManager, name)
        raise


def _owned_view_property(name, prop):
    if prop.description != _VIEW_DESCRIPTIONS[name] or not prop.is_runtime:
        return False
    if name == VIEW_PROPERTY:
        return (prop.type == 'COLLECTION'
                and prop.fixed_type.identifier == CHARACTERDESIGNER_PG_custom_vertex_group_view.bl_rna.identifier)
    if name == MODE_PROPERTY:
        return prop.type == 'ENUM' and prop.default == 'GROUPS'
    return prop.type == 'INT' and prop.default == -1 and prop.hard_min == -1


def unregister_properties():
    for name in (MODE_PROPERTY, VIEW_INDEX_PROPERTY, VIEW_PROPERTY):
        prop = bpy.types.WindowManager.bl_rna.properties.get(name)
        if prop is not None and _owned_view_property(name, prop):
            delattr(bpy.types.WindowManager, name)
