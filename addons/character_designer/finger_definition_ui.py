"""Compact reference UI and non-mutating start/end/path overlays."""
import time
from types import SimpleNamespace

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Vector

from . import finger_definition as definition
from . import finger_preview_cache as view_cache

_handles, _visible, _cache, _pending_cache = [], False, None, None
_request = None
_batches = None
_display_cache = _display_request = None


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()


def redraw(*_args, invalidate=True):
    global _cache, _pending_cache, _request, _batches, _display_cache, _display_request
    _cache = _pending_cache = None
    _display_cache = _display_request = None
    _batches = None
    _request = None
    if bpy.app.timers.is_registered(_refresh): bpy.app.timers.unregister(_refresh)
    if bpy.app.timers.is_registered(_refresh_display): bpy.app.timers.unregister(_refresh_display)
    if invalidate: view_cache.clear()
    _tag_redraw()


def _bend_changed(self, context):
    self.confirmed = False
    from . import finger_bank
    obj = finger_bank.active_object(context) if context else None
    active = finger_bank.active_state(context) if obj else None
    if active and active.as_pointer() == self.as_pointer():
        bank = obj.character_designer_finger_bank
        mate = bank.slots.get(bank.active[:-1]+('R' if bank.active[-1] == 'L' else 'L'))
        if mate and mate.guide.record:
            mate.guide.flip_bend = self.flip_bend
            mate.guide.confirmed = False
    redraw()


class CharacterDesignerFingerDefinition(PropertyGroup):
    source: PointerProperty(type=bpy.types.Object)
    record: StringProperty(options={'HIDDEN'})
    revision: StringProperty(options={'HIDDEN'})
    confirmed: BoolProperty(options={'HIDDEN'})
    use_basis: BoolProperty(default=True, options={'HIDDEN'})
    bend_source: PointerProperty(type=bpy.types.Object)
    bend_record: StringProperty(options={'HIDDEN'})
    flip_bend: BoolProperty(name='Reverse Bend', update=_bend_changed)
    pending_source: PointerProperty(type=bpy.types.Object)
    pending: StringProperty(options={'HIDDEN'})
    status: StringProperty(options={'SKIP_SAVE'})


def cached_frame(context):
    global _cache, _request
    s = definition.state(context)
    from . import finger_bank
    owner = finger_bank.active_object(context)
    if owner:
        view_cache.token(context, owner)
        slot = next((slot for slot in owner.character_designer_finger_bank.slots
                     if slot.guide.as_pointer() == s.as_pointer()), None)
        error = slot.error if slot else ''
        found = view_cache.lookup(s, error)
        if found is not None: return found
        if bpy.app.background:
            from . import finger_workflow_ui
            finger_workflow_ui.flush_reference_refresh(owner)
            return view_cache.evaluate(context, owner, s, error)
        _request = (context.scene, None, s, owner)
        if not bpy.app.timers.is_registered(_refresh): bpy.app.timers.register(_refresh, first_interval=0.)
        return None, 'Checking reference...'
    key = (context.scene.as_pointer(), s.as_pointer(), s.source.as_pointer() if s.source else 0, s.revision, s.use_basis,
           s.confirmed, s.flip_bend, hash(s.bend_record))
    now = time.monotonic()
    if _cache and _cache['key'] == key:
        return _cache['frame'], _cache['error']
    if not bpy.app.background:
        # Fingerprint validation creates unlinked data snapshots. Blender
        # forbids those writes from panel/draw callbacks, so validate on the
        # next event-loop tick and only draw the resulting cache.
        from . import finger_bank
        _request = (context.scene, key, s, finger_bank.active_object(context))
        if not bpy.app.timers.is_registered(_refresh): bpy.app.timers.register(_refresh, first_interval=.01)
        if _cache and _cache['key'] == key: return _cache['frame'], _cache['error']
        return None, 'Checking reference...'
    try: value, error = definition.frame(context), ''
    except (ValueError, RuntimeError, KeyError, ReferenceError, IndexError) as exc: value, error = None, str(exc)
    _cache = {'key': key, 'time': now, 'frame': value, 'error': error}
    return value, error


def _refresh():
    global _cache, _request
    request, _request = _request, None
    if request is None: return None
    scene, key, guide, owner = request
    try:
        context = SimpleNamespace(scene=scene, finger_definition=guide, finger_bank_object=owner)
        from . import finger_bank
        obj = finger_bank.active_object(context)
        if obj:
            b = obj.character_designer_finger_bank
            active = b.slots.get(b.active)
            if (finger_bank.active_object(bpy.context) != obj or not active or
                    active.guide.as_pointer() != guide.as_pointer() or
                    b.active.split('.')[0] not in finger_bank.selected_digits(b)):
                return None
            slot = next((slot for slot in obj.character_designer_finger_bank.slots
                         if slot.guide.as_pointer() == guide.as_pointer()), None)
            from . import finger_workflow_ui
            finger_workflow_ui.flush_reference_refresh(obj)
            value, error = view_cache.evaluate(context, obj, guide, slot.error if slot else '')
        else: value, error = definition.frame(context), ''
    except (ValueError, RuntimeError, KeyError, ReferenceError, IndexError, AttributeError) as exc:
        value, error = None, str(exc)
    _cache = {'key': key, 'time': time.monotonic(), 'frame': value, 'error': error}
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()
    return None


def show(*, invalidate=True):
    global _visible
    _visible = True
    if not _handles and not bpy.app.background:
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_lines, (), 'WINDOW', 'POST_VIEW'))
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_labels, (), 'WINDOW', 'POST_PIXEL'))
    if invalidate: redraw()
    else: _tag_redraw()


def hide(*, invalidate=False):
    global _visible
    _visible = False
    redraw(invalidate=invalidate)


def _drawable():
    if not _visible: return None
    s = definition.state(bpy.context)
    if not s.source or not s.source.visible_get(): return None
    data = cached_frame(bpy.context)[0]
    from . import finger_bank
    if data and finger_bank.active_object(bpy.context) and not data.get('internal'): return None
    return data


def display_frames(context):
    """Selected pairs and the active panel share validated read-only frames.

    No BMesh work is allowed in drawing. Only missing entries queue validation;
    modeling consumers remain uncached. Invalid/uncaptured slots are omitted.
    """
    global _display_cache, _display_request
    from . import finger_bank as bank
    obj = bank.active_object(context)
    if not obj: return ()
    b = obj.character_designer_finger_bank
    digits = bank.selected_digits(b)
    slots = [b.slots.get(d+'.'+side) for d in digits for side in ('L', 'R')]
    slots = [s for s in slots if s is not None]
    serial = view_cache.token(context, obj)
    key = (context.scene.as_pointer(), obj.as_pointer(), obj.data.as_pointer(), serial, digits,
           tuple((s.name, view_cache.key(s.guide, s.error)) for s in slots))
    if _display_cache and _display_cache['key'] == key and _display_cache['complete']:
        return _display_cache['frames']
    frames, errors, missing = [], {}, False
    for slot in slots:
        if not slot.guide.record: continue
        found = view_cache.lookup(slot.guide, slot.error)
        if found is None:
            missing = True
            continue
        data, error = found
        if error: errors[slot.name] = error
        elif data and data.get('internal'):
            data['bank_key'] = slot.name
            frames.append(data)
    _display_cache = {'key': key, 'frames': tuple(frames), 'errors': errors, 'obj': obj, 'complete': not missing}
    if missing:
        _display_request = context.scene, obj
        if bpy.app.background: _refresh_display()
        elif not bpy.app.timers.is_registered(_refresh_display): bpy.app.timers.register(_refresh_display, first_interval=0.)
    return _display_cache['frames'] if _display_cache else ()


def _refresh_display():
    global _display_cache, _display_request
    request, _display_request = _display_request, None
    if request is None: return
    scene, obj = request
    try:
        from . import finger_bank
        if finger_bank.active_object(bpy.context) != obj: return
        context = SimpleNamespace(scene=scene, finger_bank_object=obj)
        from . import finger_workflow_ui
        finger_workflow_ui.flush_reference_refresh(obj)
        view_cache.token(context, obj)
        # Always use the latest selection, not a queued selection since hidden.
        names = [d+'.'+side for d in finger_bank.selected_digits(obj.character_designer_finger_bank) for side in ('L', 'R')]
        for name in names:
            slot = obj.character_designer_finger_bank.slots.get(name)
            if not slot or not slot.guide.record: continue
            view_cache.evaluate(context, obj, slot.guide, slot.error)
        display_frames(context)
    except (ReferenceError, AttributeError): _display_cache = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()


def _drawable_frames():
    if not _visible: return ()
    from . import finger_bank
    obj = finger_bank.active_object(bpy.context)
    if obj: return display_frames(bpy.context) if obj.visible_get() else ()
    data = _drawable()
    return (data,) if data else ()


def _pending():
    global _pending_cache
    s = definition.state(bpy.context)
    from . import finger_bank
    # Bank definitions are atomic, never a completed guide plus a second
    # pending cross. Legacy standalone marker tools remain separate.
    if finger_bank.active_object(bpy.context): return None
    if not _visible or not s.pending_source or not s.pending_source.visible_get(): return None
    now = time.monotonic()
    if _pending_cache and now-_pending_cache[0] < .4: return _pending_cache[1]
    try: data = definition.pending_frame(bpy.context)
    except (ValueError, RuntimeError, KeyError, ReferenceError): data = None
    _pending_cache = (now, data)
    return data


def _side(data):
    t = data['direction']
    reference = -data['bend'] if data['bend'] is not None else Vector((0, 0, 1))
    side = t.cross(reference)
    if side.length < 1e-6: side = t.cross(Vector((1, 0, 0)))
    return side.normalized()


def _line_groups(data, pending):
    from .finger_flex import arrow
    groups = []
    if data is not None:
        path, side = data['path'], _side(data)
        width = data['length']*.07
        segments = [p for a, b in zip(path, path[1:]) for p in (a, b)]
        arrows = []
        tangent = (path[-1]-path[-2]).normalized()
        arrow(arrows, path[-1]-tangent*width*1.6, tangent, width*1.6, side)
        groups = [(segments+arrows, (.15, .7, 1., 1.)),
                  ([path[0]-side*width, path[0]+side*width], (1., .68, .12, 1.)),
                  ([path[-1]-side*width, path[-1]+side*width], (.15, 1., .75, 1.))]
        if data['bend'] is not None and not data.get('internal'):
            points = []
            arrow(points, data['midpoint'], data['bend'], width*2, data['direction'])
            groups.append((points, (1., .38, .1, 1.)))
    if pending:
        p, size = pending['point'], pending['size']
        groups.append(([q for axis in (Vector((size, 0, 0)), Vector((0, size, 0)), Vector((0, 0, size)))
                        for q in (p-axis, p+axis)], (1., .68, .12, 1.)))
    return groups


def _draw_lines():
    global _batches
    frames, pending = _drawable_frames(), _pending()
    if not frames and pending is None: return
    import gpu
    from gpu_extras.batch import batch_for_shader
    stamp = tuple(id(data) for data in frames), id(pending)
    if _batches is None or _batches[0] != stamp:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        groups = [group for data in frames for group in _line_groups(data, None)] + _line_groups(None, pending)
        _batches = (stamp, shader, [(batch_for_shader(shader, 'LINES', {'pos': points}), color) for points, color in groups])
    shader, batches = _batches[1:]
    depth, line_width, blend = gpu.state.depth_test_get(), gpu.state.line_width_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('NONE')
        gpu.state.line_width_set(3.)
        gpu.state.blend_set('ALPHA')
        shader.bind()
        for batch, color in batches:
            shader.uniform_float('color', color)
            batch.draw(shader)
    finally:
        gpu.state.depth_test_set(depth)
        gpu.state.line_width_set(line_width)
        gpu.state.blend_set(blend)


def _draw_labels():
    frames = _drawable_frames()
    pending = _pending()
    if not frames and pending is None: return
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    c = bpy.context
    if not c.region_data: return
    blf.size(0, 14)
    from . import finger_bank, finger_detect
    multiple = len({d.get('bank_key', '').split('.')[0] for d in frames}) > 1
    labels = []
    for data in frames:
        prefix = finger_detect.LABELS.get(data.get('bank_key', '').split('.')[0], '')+' ' if multiple else ''
        labels += [(data['root'], prefix+'Start', (1., .68, .12, 1.)), (data['tip'], 'End', (.15, 1., .75, 1.))]
    obj = finger_bank.active_object(c)
    if obj and obj.character_designer_finger_bank.status.startswith('Update failed'):
        labels += [(data['midpoint'], 'Previous result - update failed', (1., .35, .2, 1.)) for data in frames
                   if data.get('bank_key') == obj.character_designer_finger_bank.active]
    if pending: labels.append((pending['point'], 'Start (pending)', (1., .68, .12, 1.)))
    for point, label, color in labels:
        xy = location_3d_to_region_2d(c.region, c.region_data, point)
        if xy is not None:
            blf.position(0, xy.x+10, xy.y+8, 0)
            blf.color(0, *color)
            blf.draw(0, label)


class CHARACTERDESIGNER_OT_finger_definition(Operator):
    bl_idname = 'character_designer.finger_definition'
    bl_label = 'Finger Definition'
    # Scene reference settings aren't part of Blender's mesh Edit Mode undo.
    # Don't insert a misleading no-op undo step; X explicitly clears the guide.
    # Actual topology and bone operations retain their native UNDO operators.
    bl_options = {'REGISTER'}
    action: EnumProperty(items=[(k, label, label) for k, label in (
        ('CAPTURE', 'Capture Definition'), ('START', 'Mark Start'), ('END', 'Mark End'),
        ('CONFIRM', 'Confirm Definition'), ('SWAP', 'Swap Start / End'), ('TOP', 'Set Top Surface'),
        ('BASIS', 'Use Basis Reference'), ('SHOW', 'Show Definition'), ('HIDE', 'Hide Definition'), ('CLEAR', 'Clear Definition'))])

    def execute(self, context):
        s = definition.state(context)
        try:
            if self.action == 'CAPTURE':
                from . import finger_bank
                if finger_bank.active_object(context) and context.mode == 'EDIT_MESH': finger_bank.capture(context)
                else:
                    if context.mode in {'EDIT_ARMATURE', 'POSE'}: context.scene.character_designer_finger_setup = None
                    definition.capture(context)
                s = definition.state(context)
            elif self.action in {'START', 'END'}:
                definition.mark(context, end=self.action == 'END')
                if self.action == 'START':
                    s.status = 'Start marked. Select the end face/edge, then Mark End.'
                    show()
                    return {'FINISHED'}
            elif self.action == 'CONFIRM': definition.confirm(context)
            elif self.action == 'SWAP': definition.swap(context)
            elif self.action == 'TOP':
                from . import finger_bank
                finger_bank.validate_top(context)
                definition.set_top(context)
            elif self.action == 'BASIS': definition.basis_reference(context)
            elif self.action == 'CLEAR':
                definition.clear(context)
                hide()
                s.status = ''
                return {'FINISHED'}
            elif self.action == 'HIDE':
                hide()
                s.status = ''
                return {'FINISHED'}
            else: definition.frame(context)
            from . import finger_bank
            if self.action in {'CONFIRM', 'SWAP', 'TOP', 'BASIS'}: finger_bank.sync(context)
            show()
            s.status = 'Reference confirmed.' if s.confirmed else 'Review the arrow, then Confirm.'
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, IndexError, ReferenceError, TypeError) as exc:
            s.status = str(exc)
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw_controls(layout, context):
    from . import finger_bank, finger_bank_ui
    s = definition.state(context)
    box = layout.box()
    box.label(text='Finger · Basic Setup', icon='ORIENTATION_NORMAL')
    finger_bank_ui.draw_header(box, context)
    row = box.row(align=True)
    capture = row.row(align=True)
    capture.enabled = context.mode == 'EDIT_MESH'
    capture.operator('character_designer.finger_setup', text='Capture Detection', icon='EYEDROPPER').action = 'CAPTURE'
    owner = finger_bank.active_object(context)
    if owner and any(slot.guide.record for slot in owner.character_designer_finger_bank.slots):
        row.operator('character_designer.finger_setup', text='', icon='HIDE_OFF' if _visible else 'HIDE_ON', depress=_visible).action = 'TOGGLE'
    if s.record or s.pending:
        row.operator('character_designer.finger_setup' if finger_bank.active_object(context) else 'character_designer.finger_definition', text='', icon='X').action = 'CLEAR'
    if s.record and (not owner or owner.character_designer_finger_bank.active.split('.')[0]
                    in finger_bank.selected_digits(owner.character_designer_finger_bank)):
        data, error = cached_frame(context)
        if data and not data.get('internal'):
            box.label(text='Recapture to create an internal axis.', icon='INFO')
        elif error and error != 'Checking reference...':
            import textwrap
            for line in textwrap.wrap(error, width=43): box.label(text=line)
    if s.status:
        import textwrap
        for line in textwrap.wrap(s.status, width=43): box.label(text=line)
    if owner and _visible and _display_cache and _display_cache['obj'] == owner:
        unavailable = {name.split('.')[0] for name in _display_cache['errors']
                       if name != owner.character_designer_finger_bank.active}
        for digit in sorted(unavailable): box.label(text='Preview unavailable: '+digit.title(), icon='ERROR')


@persistent
def _invalidate(*_args):
    hide(invalidate=True)


CLASSES = (CharacterDesignerFingerDefinition, CHARACTERDESIGNER_OT_finger_definition)


def register_runtime():
    bpy.types.Scene.character_designer_finger_definition = PointerProperty(type=CharacterDesignerFingerDefinition)
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate not in handlers: handlers.append(_invalidate)
    if _geometry_changed not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_geometry_changed)


@persistent
def _geometry_changed(scene, depsgraph):
    global _cache, _pending_cache, _display_cache, _batches
    if view_cache.affected(depsgraph):
        # Keep the validated snapshot before invalidating. It is restored only
        # after the joint consumer proves complete source equality and checks
        # each guide's current domain/record; real edits still invalidate it.
        from . import finger_workflow_ui
        finger_workflow_ui.refresh(bpy.context)
        redraw()
        return
    if _cache is None and _display_cache is None: return
    source = definition.state(bpy.context).source
    if not source and _display_cache: source = _display_cache['obj']
    try:
        data = source.data if source else None
        changed = source and any((u.is_updated_geometry or u.is_updated_transform) and
                                 u.id.original in (source, data) for u in depsgraph.updates)
    except ReferenceError: changed = True
    if changed:
        _cache = _pending_cache = None
        _display_cache = _batches = None
        if _visible:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D': area.tag_redraw()


def unregister_runtime():
    global _request
    _request = None
    if bpy.app.timers.is_registered(_refresh): bpy.app.timers.unregister(_refresh)
    hide(invalidate=True)
    if _geometry_changed in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_geometry_changed)
    for h in _handles: bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
    _handles.clear()
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate in handlers: handlers.remove(_invalidate)
    if hasattr(bpy.types.Scene, 'character_designer_finger_definition'): del bpy.types.Scene.character_designer_finger_definition
