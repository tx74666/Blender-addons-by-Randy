"""Transient multi-row selection shared by two independent native catalogs."""
import bmesh
import json
import time


def _token(mesh):
    return str(getattr(mesh, 'session_uid', None) or mesh.as_pointer())


def _valid(mesh, name, prefix):
    attribute = mesh.attributes.get(name)
    return (attribute is not None and attribute.domain == 'POINT' and attribute.data_type == 'INT'
            and name.startswith(prefix) and len(name) > len(prefix))


def snapshot(rows, mesh, valid_names, active=''):
    names = set(valid_names)
    token = _token(mesh)
    current = [row for row in rows if row.mesh_token == token and row.native_name in names]
    if current:
        return {row.native_name for row in current if row.selected}
    return {active} if active in names else set()


def chosen_names(rows, mesh, prefix):
    token = _token(mesh)
    current = [row for row in rows if row.mesh_token == token and _valid(mesh, row.native_name, prefix)]
    if current:
        return tuple(row.native_name for row in current if row.selected)
    active = mesh.attributes.active
    return (active.name,) if active is not None and _valid(mesh, active.name, prefix) else ()


def anchor_name(rows, mesh, valid_names):
    token, names = _token(mesh), set(valid_names)
    return next((row.native_name for row in rows
                 if row.mesh_token == token and row.native_name in names and row.anchor), '')


def visible_names(ui_list, context, data, property_name):
    """Match the native list's filtered, sorted and reversed display order."""
    rows = getattr(data, property_name)
    flags, order = ui_list.filter_items(context, data, property_name)
    indices = [index for index in range(len(rows))
               if not flags or (flags[index] & ui_list.bitflag_filter_item
                                and not flags[index] & ui_list.bitflag_item_never_show)]
    if order:
        indices.sort(key=lambda index: order[index])
    if ui_list.use_filter_sort_reverse:
        indices.reverse()
    return json.dumps([rows[index].native_name for index in indices])


class RowRenameClick:
    """Listen only during this click's double-click interval, without owning input."""

    def wait_for_double_click(self, context, event, mesh):
        if event.ctrl or event.shift or event.type != 'LEFTMOUSE':
            return {'FINISHED'}
        delay = context.preferences.inputs.mouse_double_click_time / 1000
        self._click_window = context.window.as_pointer()
        self._click_area = context.area.as_pointer()
        self._click_mesh = _token(mesh)
        self._click_xy = (event.mouse_x, event.mouse_y)
        self._click_slop = max(4, getattr(context.preferences.inputs, 'drag_threshold_mouse', 3))
        self._click_deadline = time.monotonic() + delay
        self._click_timer = context.window_manager.event_timer_add(delay, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        timer = getattr(self, '_click_timer', None)
        if timer is not None:
            context.window_manager.event_timer_remove(timer)
            self._click_timer = None

    def modal(self, context, event):
        expired = time.monotonic() > self._click_deadline
        same_view = (context.window.as_pointer() == self._click_window
                     and context.area is not None and context.area.as_pointer() == self._click_area)
        nearby = max(abs(event.mouse_x - self._click_xy[0]),
                     abs(event.mouse_y - self._click_xy[1])) <= self._click_slop
        second_press = event.type == 'LEFTMOUSE' and event.value in {'PRESS', 'DOUBLE_CLICK'}
        if not expired and same_view and nearby and not event.ctrl and not event.shift and second_press:
            self.cancel(context)
            obj = context.active_object
            if obj is not None and obj.type == 'MESH' and _token(obj.data) == self._click_mesh:
                try:
                    if self.rename_row(context):
                        return {'FINISHED'}
                except (RuntimeError, ValueError, ReferenceError) as exc:
                    self.report({'WARNING'}, str(exc))
            return {'FINISHED', 'PASS_THROUGH'}
        if (expired or not same_view or event.ctrl or event.shift
                or event.type in {'ESC', 'RIGHTMOUSE', 'MIDDLEMOUSE'}
                or (event.type == 'MOUSEMOVE' and not nearby) or second_press):
            self.cancel(context)
            return {'FINISHED', 'PASS_THROUGH'}
        return {'PASS_THROUGH'}


def choose(rows, mesh, prefix, name, extend=False, *, range_select=False, visible_names=None):
    if not _valid(mesh, name, prefix):
        raise ValueError('Select a row from this list.')
    token = _token(mesh)
    current = [row for row in rows if row.mesh_token == token and _valid(mesh, row.native_name, prefix)]
    target = next((row for row in current if row.native_name == name), None)
    if target is None:
        raise ValueError('The selection list is out of date.')
    if range_select:
        valid = {row.native_name for row in current}
        order = list(dict.fromkeys(visible_names if visible_names is not None else
                                  (row.native_name for row in current)))
        order = [item for item in order if item in valid]
        if name not in order:
            raise ValueError('The selected row is no longer visible.')
        anchor = anchor_name(rows, mesh, valid)
        if anchor not in order:
            active = mesh.attributes.active
            anchor = active.name if active is not None and active.name in order else name
        start, end = sorted((order.index(anchor), order.index(name)))
        selected = set(order[start:end + 1])
        for row in current:
            row.selected = row.native_name in selected or (extend and row.selected)
        if not any(row.anchor and row.native_name == anchor for row in current):
            for row in current:
                row.anchor = row.native_name == anchor
    elif extend:
        target.selected = not target.selected
    else:
        for row in current:
            row.selected = row.native_name == name
    if not range_select:
        for row in current:
            row.anchor = row.native_name == name
    return next(index for index, row in enumerate(rows) if row.native_name == name and row.mesh_token == token)


def rename_cache(window_manager, view_property, mesh, old, new):
    rows = getattr(window_manager, view_property, ())
    token = _token(mesh)
    for row in rows:
        if row.mesh_token == token and row.native_name == old:
            row.native_name = new


def new_row(window_manager, view_property, index_property, mesh, name, prefix):
    """Choose a newly created row without repurposing another mesh's RNA rows."""
    if not _valid(mesh, name, prefix):
        raise ValueError('Select a row from this list.')
    rows = getattr(window_manager, view_property)
    token = _token(mesh)
    for row in rows:
        if row.mesh_token == token:
            row.selected = False
            row.anchor = False
    row = rows.add()
    row.native_name, row.mesh_token, row.selected, row.anchor = name, token, True, True
    setattr(window_manager, index_property, len(rows) - 1)


def select_vertices(context, mesh, names, prefix, *, select, extend=False):
    names = tuple(names)
    if not names:
        raise ValueError('Select one or more list icons first.')
    if not all(_valid(mesh, name, prefix) for name in names):
        raise ValueError('Use selections from this list only.')
    if not mesh.is_editmode:
        raise ValueError('Use Mesh Edit Mode.')
    bm = bmesh.from_edit_mesh(mesh)
    layers = [bm.verts.layers.int.get(name) for name in names]
    if any(layer is None for layer in layers):
        raise ValueError('A selection layer is missing.')
    context.tool_settings.mesh_select_mode = (True, False, False)
    bm.select_mode = {'VERT'}
    if select and not extend:
        for elements in (bm.faces, bm.edges, bm.verts):
            for element in elements:
                element.select_set(False)
    for vertex in bm.verts:
        if not vertex.hide and any(vertex[layer] != 0 for layer in layers):
            vertex.select_set(select)
    if select and not extend:
        bm.select_history.clear()
    else:
        bm.select_history.validate()
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
