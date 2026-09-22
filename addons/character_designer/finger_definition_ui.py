"""Compact reference UI and non-mutating start/end/path overlays."""
import time
from contextlib import contextmanager
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
_reference_write_depth = 0


@contextmanager
def reference_write():
    """Commit complete references without running interactive bend callbacks.

    Callers own the final invalidation after the reference transaction, so no
    redraw can expose a partially written source or overwrite a retained mate.
    """
    global _reference_write_depth
    _reference_write_depth += 1
    try:
        yield
    finally:
        _reference_write_depth -= 1


def overlays_enabled(context=None):
    """Scene-wide display intent, independent of each overlay's own eye."""
    context = context or bpy.context
    return context.scene.character_designer_finger_definition.overlays_enabled


def _overlays_changed(self, context):
    from . import finger_bone_tools, finger_loop_marks_ui
    context = context or bpy.context
    finger_loop_marks_ui.refresh(context)
    cancel_pending()
    if self.overlays_enabled:
        show(invalidate=False)
        finger_bone_tools.resume_overlays(context)
    else:
        finger_bone_tools.suspend_overlays()
        from . import finger_flex
        finger_flex._visible = False


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()


def cancel_pending():
    """Visibility changes retain cached frames and GPU batches."""
    global _request, _display_request
    _request = _display_request = None
    for callback in (_refresh, _refresh_display):
        if bpy.app.timers.is_registered(callback): bpy.app.timers.unregister(callback)
    _tag_redraw()


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
    if _reference_write_depth: return
    self.confirmed = False
    redraw()

    from . import finger_bone_tools
    finger_bone_tools.invalidate(context)


class CharacterDesignerFingerDefinition(PropertyGroup):
    overlays_enabled: BoolProperty(name='Show Finger Overlays', default=True, update=_overlays_changed,
        description='Show or hide finger references, loop marks and bend overlays; retain the cached guides')
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
    if not overlays_enabled(context): return None, ''
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
        return view_cache.evaluate(context, owner, s, error)
    key = (context.scene.as_pointer(), s.as_pointer(), s.source.as_pointer() if s.source else 0, s.revision, s.use_basis,
           s.record, s.confirmed, s.flip_bend, hash(s.bend_record),
           tuple(tuple(row) for row in s.source.matrix_world) if s.source else ())
    now = time.monotonic()
    if _cache and _cache['key'] == key:
        return _cache['frame'], _cache['error']
    try: value, error = definition.saved_frame(context), ''
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
        if not overlays_enabled(context): return None
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
            value, error = view_cache.evaluate(context, obj, guide, slot.error if slot else '')
        else: value, error = definition.saved_frame(context), ''
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
    if not _visible or not overlays_enabled(): return None
    s = definition.state(bpy.context)
    if not s.source or not s.source.visible_get(): return None
    data = cached_frame(bpy.context)[0]
    from . import finger_bank
    if data and finger_bank.active_object(bpy.context) and not data.get('internal'): return None
    return data


def display_frames(context):
    """Selected fingers share frozen saved frames on the active side.

    Missing entries only decode saved metadata; editing never queues validation.
    Modeling consumers remain separate and validate on explicit actions.
    """
    global _display_cache, _display_request
    if not overlays_enabled(context): return ()
    from . import finger_bank as bank
    obj = bank.active_object(context)
    if not obj: return ()
    b = obj.character_designer_finger_bank
    digits = bank.selected_digits(b)
    slots = [b.slots.get(key) for key in bank.display_keys(b)]
    slots = [s for s in slots if s is not None]
    serial = view_cache.token(context, obj)
    key = (context.scene.as_pointer(), obj.as_pointer(), obj.data.as_pointer(), serial, digits, bank.display_side(b),
           tuple((s.name, view_cache.key(s.guide, s.error)) for s in slots))
    if _display_cache and _display_cache['key'] == key and _display_cache['complete']:
        return _display_cache['frames']
    frames, errors = [], {}
    for slot in slots:
        if not slot.guide.record: continue
        found = view_cache.lookup(slot.guide, slot.error)
        if found is None:
            found = view_cache.evaluate(context, obj, slot.guide)
        data, error = found
        if error: errors[slot.name] = error
        elif data and data.get('internal'):
            data['bank_key'] = slot.name
            frames.append(data)
    # Saved-data decoding can advance the shared serial; retain the final token.
    key = (key[0], key[1], key[2], view_cache.token(context, obj), *key[4:])
    _display_cache = {'key': key, 'frames': tuple(frames), 'errors': errors, 'obj': obj, 'complete': True}
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
        if not overlays_enabled(context): return
        view_cache.token(context, obj)
        # Always use the latest selection, not a queued selection since hidden.
        names = finger_bank.display_keys(obj.character_designer_finger_bank)
        view_cache.evaluate_many(context, obj, names)
        display_frames(context)
    except (ReferenceError, AttributeError): _display_cache = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()


def _drawable_frames():
    if not _visible or not overlays_enabled(): return ()
    from . import finger_bank
    obj = finger_bank.active_object(bpy.context)
    if obj: return display_frames(bpy.context) if obj.visible_get() else ()
    data = _drawable()
    return (data,) if data else ()


def _pending():
    global _pending_cache
    if not overlays_enabled(): return None
    s = definition.state(bpy.context)
    from . import finger_bank
    # Bank definitions are atomic, never a completed guide plus a second
    # pending cross. Legacy standalone marker tools remain separate.
    if finger_bank.active_object(bpy.context): return None
    if not _visible or not s.pending_source or not s.pending_source.visible_get(): return None
    key = (s.pending_source.as_pointer(), s.pending,
           tuple(tuple(row) for row in s.pending_source.matrix_world))
    if _pending_cache and _pending_cache[0] == key: return _pending_cache[1]
    try: data = definition.saved_pending_frame(bpy.context)
    except (ValueError, RuntimeError, KeyError, ReferenceError, TypeError, IndexError): data = None
    _pending_cache = (key, data)
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
        ('OVERLAYS', 'Toggle All Finger Overlays'),
        ('BASIS', 'Use Basis Reference'), ('SHOW', 'Show Definition'), ('HIDE', 'Hide Definition'), ('CLEAR', 'Clear Definition'))])

    def execute(self, context):
        if self.action == 'OVERLAYS':
            master = context.scene.character_designer_finger_definition
            master.overlays_enabled = not master.overlays_enabled
            return {'FINISHED'}
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
                from . import finger_bank
                paired = finger_bank.active_object(context) is not None
                definition.clear(context)
                if not paired: hide()
                return {'FINISHED'}
            elif self.action == 'HIDE':
                hide()
                s.status = ''
                return {'FINISHED'}
            elif self.action == 'SHOW':
                show(invalidate=False)
                return {'FINISHED'}
            from . import finger_bank
            if self.action in {'CONFIRM', 'SWAP', 'TOP', 'BASIS'}: finger_bank.sync(context)
            if self.action not in {'SHOW', 'HIDE'}:
                from . import finger_bone_tools
                finger_bone_tools.invalidate(context)
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
        enabled = overlays_enabled(context)
        row.operator('character_designer.finger_definition', text='', icon='HIDE_OFF' if enabled else 'HIDE_ON', depress=enabled).action = 'OVERLAYS'
    active_slot = owner.character_designer_finger_bank.slots.get(owner.character_designer_finger_bank.active) if owner else None
    if s.record or s.pending or (active_slot and (finger_bank.configured(active_slot) or active_slot.error or active_slot.bones)):
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


@persistent
def _resume(*_args):
    # Restore saved display intent without consulting edited geometry.
    from . import finger_bank
    finger_bank.discard_legacy_view_errors()
    if overlays_enabled(): show(invalidate=False)


CLASSES = (CharacterDesignerFingerDefinition, CHARACTERDESIGNER_OT_finger_definition)


def register_runtime():
    bpy.types.Scene.character_designer_finger_definition = PointerProperty(type=CharacterDesignerFingerDefinition)
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate not in handlers: handlers.append(_invalidate)
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _resume not in handlers: handlers.append(_resume)


@persistent
def _geometry_changed(scene, depsgraph):
    """Legacy hook kept inert; saved visuals never monitor mesh edits."""
    return None


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
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _resume in handlers: handlers.remove(_resume)
    if hasattr(bpy.types.Scene, 'character_designer_finger_definition'): del bpy.types.Scene.character_designer_finger_definition
