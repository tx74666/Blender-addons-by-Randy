"""Compact capture/confirm UI and non-mutating start/end/path overlays."""
import time
from types import SimpleNamespace

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Vector

from . import finger_definition as definition

_handles, _visible, _cache, _pending_cache = [], False, None, None
_request = None


def redraw(*_args):
    global _cache, _pending_cache, _request
    _cache = _pending_cache = None
    _request = None
    if bpy.app.timers.is_registered(_refresh): bpy.app.timers.unregister(_refresh)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()


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
    key = (context.scene.as_pointer(), s.as_pointer(), s.source.as_pointer() if s.source else 0, s.revision, s.use_basis,
           s.confirmed, s.flip_bend, hash(s.bend_record))
    now = time.monotonic()
    if _cache and _cache['key'] == key and now-_cache['time'] < .4:
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
            obj.character_designer_finger_bank.needs_recheck = 'Geometry changed: Recheck symmetry.' if finger_bank.dirty(context) else ''
        value, error = definition.frame(context), ''
    except (ValueError, RuntimeError, KeyError, ReferenceError, IndexError, AttributeError) as exc:
        value, error = None, str(exc)
    _cache = {'key': key, 'time': time.monotonic(), 'frame': value, 'error': error}
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()
    return None


def show():
    global _visible
    _visible = True
    if not _handles and not bpy.app.background:
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_lines, (), 'WINDOW', 'POST_VIEW'))
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_labels, (), 'WINDOW', 'POST_PIXEL'))
    redraw()


def hide():
    global _visible
    _visible = False
    redraw()


def _drawable():
    if not _visible: return None
    s = definition.state(bpy.context)
    if not s.source or not s.source.visible_get(): return None
    data = cached_frame(bpy.context)[0]
    from . import finger_bank
    if data and finger_bank.active_object(bpy.context) and not data.get('internal'): return None
    return data


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


def _draw_lines():
    data = _drawable()
    pending = _pending()
    if data is None and pending is None: return
    import gpu
    from gpu_extras.batch import batch_for_shader
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
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    depth, line_width, blend = gpu.state.depth_test_get(), gpu.state.line_width_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('NONE')
        gpu.state.line_width_set(3.)
        gpu.state.blend_set('ALPHA')
        shader.bind()
        for points, color in groups:
            shader.uniform_float('color', color)
            batch_for_shader(shader, 'LINES', {'pos': points}).draw(shader)
    finally:
        gpu.state.depth_test_set(depth)
        gpu.state.line_width_set(line_width)
        gpu.state.blend_set(blend)


def _draw_labels():
    data = _drawable()
    pending = _pending()
    if data is None and pending is None: return
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    c = bpy.context
    if not c.region_data: return
    blf.size(0, 14)
    labels = [(data['root'], 'Start', (1., .68, .12, 1.)),
              (data['tip'], 'End', (.15, 1., .75, 1.))] if data else []
    from . import finger_bank
    obj = finger_bank.active_object(c)
    if data and obj and obj.character_designer_finger_bank.status.startswith('Update failed'):
        labels.append((data['midpoint'], 'Previous result - update failed', (1., .35, .2, 1.)))
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
    if s.record and finger_bank.active_object(context):
        row.operator('character_designer.finger_setup', text='', icon='HIDE_OFF' if _visible else 'HIDE_ON', depress=_visible).action = 'TOGGLE'
    if s.record or s.pending:
        row.operator('character_designer.finger_setup' if finger_bank.active_object(context) else 'character_designer.finger_definition', text='', icon='X').action = 'CLEAR'
    if s.record:
        data, error = cached_frame(context)
        if data and not data.get('internal'):
            box.label(text='Recapture to create an internal axis.', icon='INFO')
        elif error and error != 'Checking reference...':
            import textwrap
            for line in textwrap.wrap(error, width=43): box.label(text=line)
    if s.status:
        import textwrap
        for line in textwrap.wrap(s.status, width=43): box.label(text=line)


@persistent
def _invalidate(*_args):
    hide()


CLASSES = (CharacterDesignerFingerDefinition, CHARACTERDESIGNER_OT_finger_definition)


def register_runtime():
    bpy.types.Scene.character_designer_finger_definition = PointerProperty(type=CharacterDesignerFingerDefinition)
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate not in handlers: handlers.append(_invalidate)


def unregister_runtime():
    global _request
    _request = None
    if bpy.app.timers.is_registered(_refresh): bpy.app.timers.unregister(_refresh)
    hide()
    for h in _handles: bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
    _handles.clear()
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate in handlers: handlers.remove(_invalidate)
    if hasattr(bpy.types.Scene, 'character_designer_finger_definition'): del bpy.types.Scene.character_designer_finger_definition
