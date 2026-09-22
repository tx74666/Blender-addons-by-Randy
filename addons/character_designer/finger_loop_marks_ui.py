"""Small saved loop guides: explicit updates, retained GPU batches, no mesh monitor."""
import json
from functools import lru_cache

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty
from bpy.types import Operator

from . import finger_bank as bank, finger_definition_ui as guides
from . import finger_loop_marks as marks

_cache = {}
_handles = []
_YELLOW = (1., 1., 0., 1.)
_KEYS = frozenset(digit+'.'+side for digit in bank.detect.DIGITS for side in ('L', 'R'))
_ERRORS = (ValueError, RuntimeError, ReferenceError, KeyError, IndexError, TypeError)


@lru_cache(maxsize=32)
def _mark_flags(raw):
    """Immutable panel state; never cache the mutable records used by writers."""
    try:
        saved = json.loads(raw)
        if not isinstance(saved, dict): return ()
    except (ValueError, TypeError):
        return ()
    return tuple((key, frozenset(number for number in ('1', '2')
                                if isinstance(record.get(number), dict)))
                 for key, record in saved.items() if key in _KEYS and isinstance(record, dict))


def _segments(paths):
    return tuple(point for path in paths
                 for pair in zip(path, path[1:]+path[:1]) for point in pair)


def refresh(context):
    """Load tiny saved polylines on a user action, never inspect geometry."""
    obj = bank.active_object(context)
    if obj is None: return
    pointer, raw = obj.as_pointer(), obj.get(marks.PROPERTY, '')
    previous = _cache.get(pointer)
    if previous and previous['raw'] == raw and previous['data'] == obj.data:
        return
    entries = {}
    saved = marks._saved(obj)
    for key in saved:
        if key not in _KEYS: continue
        paths = tuple(marks.points(obj, key, saved=saved).values())
        if not paths: continue
        # Both loops share one color and one retained GPU batch per finger.
        entries[key] = dict(groups=[(_segments(paths), _YELLOW)], batches=None, source=key)
    _cache[pointer] = dict(obj=obj, data=obj.data, raw=raw, entries=entries)
    while len(_cache) > 16: _cache.pop(next(iter(_cache)))
    if entries and not _handles and not bpy.app.background:
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW'))
    guides._tag_redraw()


def visible(context=None):
    """Only constant-size RNA state and cached buffers are read during drawing."""
    context = context or bpy.context
    if not guides.overlays_enabled(context): return ()
    obj = bank.active_object(context)
    if obj is None: return ()
    cached = _cache.get(obj.as_pointer())
    if not cached or cached['data'] != obj.data or not obj.visible_get(): return ()
    # These are explicitly saved guide locations. Mesh editing never launches a
    # rebuild; Align validates the current loops before any bone can move.
    state = obj.character_designer_finger_bank
    side = bank.display_side(state)
    return tuple((obj, cached['entries'][key])
                 for key in (digit+'.'+side for digit in bank.selected_digits(state))
                 if key in cached['entries'])


def _draw():
    entries = visible()
    if not entries: return
    import gpu
    from gpu_extras.batch import batch_for_shader
    depth = gpu.state.depth_test_get()
    width = gpu.state.line_width_get()
    blend = gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('NONE')
        gpu.state.line_width_set(3)
        gpu.state.blend_set('ALPHA')
        for obj, entry in entries:
            if entry['batches'] is None:
                shader = gpu.shader.from_builtin('UNIFORM_COLOR')
                entry['batches'] = shader, [(batch_for_shader(shader, 'LINES', {'pos': points}), color)
                                            for points, color in entry['groups']]
            shader, batches = entry['batches']
            with gpu.matrix.push_pop():
                gpu.matrix.multiply_matrix(obj.matrix_world)
                shader.bind()
                for batch, color in batches:
                    shader.uniform_float('color', color)
                    batch.draw(shader)
    finally:
        gpu.state.depth_test_set(depth)
        gpu.state.line_width_set(width)
        gpu.state.blend_set(blend)


class CHARACTERDESIGNER_OT_finger_loop_marks(Operator):
    bl_idname = 'character_designer.finger_loop_marks'
    bl_label = 'Finger Loop Marks'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=[(key, label, '') for key, label in (
        ('MARK', 'Mark'),
        ('ALIGN', 'Align Joints to Marks'), ('CLEAR', 'Clear Loop Marks'))])

    @classmethod
    def description(cls, context, props):
        if props.action == 'MARK':
            return ('Mark one or both selected joint loops in bright yellow; order is automatic. '
                    'A new single loop replaces the nearest existing mark when both are set. '
                    'Select both loops to replace the pair. No geometry or weights are changed')
        if props.action == 'ALIGN':
            return ('Place joints at the marked cross sections. Four fingers stay on their root-to-tip axis; '
                    'thumb follows its curved chain. Keep root, tip and Roll. '
                    'Only the current side is changed')
        return 'Clear this side\'s loop guides. Keep the mesh and bones'

    def execute(self, context):
        obj = bank.active_object(context)
        try:
            if self.action == 'MARK':
                marks.mark_selected(context)
                message = 'Joint loops marked.'
            elif self.action == 'CLEAR':
                marks.clear(context)
                message = ''
            else:
                result = marks.align(context)
                message = f"Aligned joints in {result['chains']} chain(s). Root, tip and Roll kept."
                from . import finger_bone_tools
                finger_bone_tools.invalidate(context)
            if obj: obj.character_designer_finger_bank.bone_status = message
            refresh(context)
            if message: self.report({'INFO'}, message)
            return {'FINISHED'}
        except _ERRORS as exc:
            if obj: obj.character_designer_finger_bank.bone_status = str(exc)
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw_controls(mark_cell, align_cell, context):
    """Two compact cells within the Bone Chain panel's two-row layout."""
    obj = bank.active_object(context)
    state = obj.character_designer_finger_bank if obj else None
    slot = state.slots.get(state.active) if state else None
    # Panel state reads metadata only; geometry is checked by explicit actions.
    flags = dict(_mark_flags(obj.get(marks.PROPERTY, ''))) if obj else {}
    stored = flags.get(state.active, frozenset()) if state else frozenset()
    row = mark_cell.row(align=True)
    row.enabled = bool(slot and slot.guide.record and context.mode == 'EDIT_MESH')
    row.operator('character_designer.finger_loop_marks', text='Mark',
                 icon='CHECKMARK' if len(stored) == 2 else 'NONE',
                 depress=bool(stored)).action = 'MARK'
    clear = mark_cell.row(align=True)
    clear.enabled = bool(stored)
    clear.operator('character_designer.finger_loop_marks', text='', icon='X').action = 'CLEAR'
    row = align_cell.row(align=True)
    apply = row.row(align=True)
    apply.enabled = bool(slot and slot.guide.record and '1' in stored and '2' in stored)
    apply.operator('character_designer.finger_loop_marks', text='Align Joints').action = 'ALIGN'


@persistent
def _reset(*_):
    _cache.clear()
    _mark_flags.cache_clear()


@persistent
def _restore(*_):
    _reset()
    if getattr(bpy.context, 'scene', None) is not None:
        refresh(bpy.context)


CLASSES = (CHARACTERDESIGNER_OT_finger_loop_marks,)


def register_runtime():
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _reset not in handlers: handlers.append(_reset)
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _restore not in handlers: handlers.append(_restore)
    # Blender deliberately hides scene/data while enabling an add-on. Mark and
    # load actions (and the completed reload) populate these display records.
    if getattr(bpy.context, 'scene', None) is not None:
        refresh(bpy.context)


def unregister_runtime():
    _reset()
    for handle in _handles: bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handles.clear()
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _reset in handlers: handlers.remove(_reset)
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _restore in handlers: handlers.remove(_restore)
