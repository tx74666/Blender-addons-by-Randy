"""Independent finger bone actions and event-cached bend guides.

No mesh preparation, source snapshots, topology plans or weight generation.
Drawing and invalidation only consume metadata and small retained line buffers.
"""
from collections import OrderedDict
import textwrap
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator
from mathutils import Vector

from . import finger_bank as bank, finger_definition as definition
from . import finger_definition_ui as guides, finger_targets as targets
from . import finger_chain, finger_flex as flex

_preview = None
_intent = None
_request = None
_building = False
_handles = []
_saved_previews = OrderedDict()
_ERRORS = (ValueError, RuntimeError, ReferenceError, KeyError, IndexError, AttributeError)


def _matrix(value):
    return tuple(tuple(row) for row in value)


def _key(context, obj, all_fingers):
    """RNA metadata only; ordinary mesh edits retain saved preview buffers."""
    state = obj.character_designer_finger_bank
    rig = context.scene.character_designer_setup.rig
    rig_key = (rig.as_pointer(), rig.data.as_pointer(), rig.mode, _matrix(rig.matrix_world)) if rig else None
    return (context.scene.as_pointer(), obj.as_pointer(), obj.data.as_pointer(),
            obj.mode, obj.active_shape_key_index, _matrix(obj.matrix_world), rig_key,
            bank.display_side(state), all_fingers,
            () if all_fingers else (state.active, bank.selected_digits(state)),
            state.survey, tuple((slot.name, slot.error, slot.bones,
                slot.guide.record, slot.guide.revision, slot.guide.flip_bend,
                slot.guide.use_basis, slot.guide.confirmed, slot.guide.bend_record,
                slot.guide.source.as_pointer() if slot.guide.source else 0,
                slot.guide.bend_source.as_pointer() if slot.guide.bend_source else 0)
                for slot in state.slots))


def _dependencies(obj, rig):
    values = {obj, obj.data}
    owners = {rig}
    for slot in obj.character_designer_finger_bank.slots:
        owners.update((slot.guide.source, slot.guide.bend_source))
    owners.update(m.mirror_object for m in obj.modifiers if m.type == 'MIRROR')
    for owner in owners:
        if owner is None: continue
        values.add(owner)
        if owner.data:
            values.add(owner.data)
            keys = getattr(owner.data, 'shape_keys', None)
            if keys: values.add(keys)
    return {value.as_pointer() for value in values}


def _cancel():
    global _request
    _request = None
    if bpy.app.timers.is_registered(_refresh): bpy.app.timers.unregister(_refresh)


def suspend_overlays():
    """The master eye hides guides without discarding intent or cached lines."""
    global _preview
    _preview = None
    _cancel()
    guides._tag_redraw()


def hide():
    global _intent
    _intent = None
    suspend_overlays()


def _cached(context, obj, all_fingers):
    global _preview
    key = _key(context, obj, all_fingers)
    cached = _saved_previews.get(key)
    if cached is None: return False
    _preview = cached
    _saved_previews.move_to_end(key)
    _cancel()
    guides._tag_redraw()
    return True


def refresh_selection(context):
    """Switch cached side/selection immediately, or coalesce one explicit build."""
    global _preview, _request
    if _building: return
    _preview = None
    _cancel()
    if not _intent or not guides.overlays_enabled(context): return
    obj, all_fingers = _intent
    try:
        if bank.active_object(context) != obj: return
        if _cached(context, obj, all_fingers): return
        _request = (obj, all_fingers)
        bpy.app.timers.register(_refresh, first_interval=0.)
    except _ERRORS:
        _request = None


def resume_overlays(context):
    refresh_selection(context)


def invalidate(context=None):
    """Cheap event invalidation; never resolve references or scan geometry here."""
    if _building: return
    _saved_previews.clear()
    suspend_overlays()
    if context is not None: refresh_selection(context)


def _refresh():
    global _request
    request, _request = _request, None
    if request is None or request != _intent or not guides.overlays_enabled(): return
    try:
        obj, all_fingers = request
        if bank.active_object(bpy.context) == obj:
            show_bend(bpy.context, all_fingers=all_fingers)
    except _ERRORS:
        # No invalid reference is displayed and no self-triggering status write.
        # The explicit Preview Bend action reports the actionable error.
        pass


def _install(context, obj, rig, groups, labels, all_fingers):
    global _preview
    state = obj.character_designer_finger_bank
    _preview = dict(obj=obj, rig=rig, groups=groups, labels=labels, batches=None,
                    all=all_fingers, active=state.active, side=bank.display_side(state),
                    selected=bank.selected_digits(state), mode=obj.mode,
                    rig_mode=rig.mode if rig else None, dependencies=_dependencies(obj, rig))
    key = _key(context, obj, all_fingers)
    _saved_previews[key] = _preview
    _saved_previews.move_to_end(key)
    while len(_saved_previews) > 12: _saved_previews.popitem(last=False)
    if not _handles and not bpy.app.background:
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW'))
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_labels, (), 'WINDOW', 'POST_PIXEL'))
    guides._tag_redraw()


def show_bend(context, *, all_fingers=False):
    """Resolve on demand only; cached toggles and viewport drawing do no work."""
    global _intent, _building
    from .finger_bones import _head, _tail
    obj = bank.active_object(context)
    if not obj: raise ValueError('Capture Finger Basic Setup first.')
    _intent = (obj, all_fingers)
    if not guides.overlays_enabled(context): return
    if _cached(context, obj, all_fingers): return
    _building = True
    try:
        state = obj.character_designer_finger_bank
        rig = None
        try: _, rig = targets.owner(context)
        except ValueError: pass
        candidates = targets.index(rig) if rig else {}
        digits = bank.detect.DIGITS if all_fingers else bank.selected_digits(state)
        from . import finger_preview_cache
        frames = finger_preview_cache.evaluate_many(context, obj, [d+'.'+bank.display_side(state) for d in digits])
        groups, labels = [], []
        inverse = obj.matrix_world.inverted()
        for digit in digits:
            slot = state.slots.get(digit+'.'+bank.display_side(state))
            if slot is None or not slot.guide.record or targets.reference_error(obj, slot.name): continue
            chain = targets.resolve(obj, rig, slot.name, candidates) if rig else ()
            data, error = frames.get(slot.name, (None, 'No valid reference to preview.'))
            if error: raise ValueError(error)
            if data['bend'] is None:
                raise ValueError(data['bend_error'] or 'Bone Roll needs a reliable bend side. Capture a longitudinal top-surface strip; the internal axis itself is already usable.')
            forward, bend, arcs, axes, current = [], [], [], [], []
            rows = []
            for bone in chain:
                head, tail = rig.matrix_world @ _head(bone), rig.matrix_world @ _tail(bone)
                x = bone.x_axis if rig.mode == 'EDIT' else bone.matrix_local.to_3x3().col[0]
                rows.append((head, tail, (rig.matrix_world.to_3x3() @ x).normalized()))
            if not rows: rows = [(data['root'], data['tip'], None)]
            for head, tail, x in rows:
                tangent, inward, axis = flex.frame(tail-head, data['bend'])
                length = (tail-head).length
                flex.arrow(forward, head, tangent, length, inward)
                flex.arrow(bend, head, inward, length*.35, tangent)
                flex.arc(arcs, head, tangent, axis, length)
                axes += [head-axis*length*.2, head+axis*length*.2]
                if x is not None: current += [head-x*length*.15, head+x*length*.15]
            for points, color in zip((forward, bend, arcs, axes, current),
                    ((.1, .65, 1, 1), (1, .4, .05, 1), (.2, 1, .3, 1),
                     (.8, .35, 1, 1), (1, .12, .12, 1))):
                if points: groups.append(([tuple(inverse @ Vector(p)) for p in points], color))
            labels.append((inverse @ data['root'], slot.name, (.1, .65, 1, 1)))
        if not groups: raise ValueError('No valid bend definitions to preview.')
        # Drain notifications from Edit Mode snapshots before publishing. The
        # geometry listener suppresses these self-induced updates while building.
        context.view_layer.update()
        _install(context, obj, rig, groups, labels, all_fingers)
    finally:
        _building = False


def _valid(context=None):
    if _preview is None: return None
    context = context or bpy.context
    if not guides.overlays_enabled(context): return None
    try:
        p = _preview
        obj, rig = p['obj'], p['rig']
        state = obj.character_designer_finger_bank
        if context.object not in (obj, rig) or not obj.visible_get(): return None
        if obj.mode != p['mode'] or (rig.mode if rig else None) != p['rig_mode']: return None
        if bank.display_side(state) != p['side']: return None
        if not p['all'] and (state.active != p['active'] or bank.selected_digits(state) != p['selected']): return None
        return p
    except ReferenceError:
        return None


def _draw():
    p = _valid()
    if p is None: return
    import gpu
    from gpu_extras.batch import batch_for_shader
    if p['batches'] is None:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        p['batches'] = shader, [(batch_for_shader(shader, 'LINES', {'pos': points}), color)
                              for points, color in p['groups']]
    shader, batches = p['batches']
    depth, width, blend = gpu.state.depth_test_get(), gpu.state.line_width_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('NONE'); gpu.state.line_width_set(2); gpu.state.blend_set('ALPHA')
        with gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(p['obj'].matrix_world)
            shader.bind()
            for batch, color in batches:
                shader.uniform_float('color', color)
                batch.draw(shader)
    finally:
        gpu.state.depth_test_set(depth); gpu.state.line_width_set(width); gpu.state.blend_set(blend)


def _labels():
    p = _valid()
    if p is None or not bpy.context.region_data: return
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    blf.size(0, 14)
    for point, text, color in p['labels']:
        xy = location_3d_to_region_2d(bpy.context.region, bpy.context.region_data, p['obj'].matrix_world @ point)
        if xy:
            blf.position(0, xy.x+6, xy.y+6, 0); blf.color(0, *color); blf.draw(0, text)


class CHARACTERDESIGNER_OT_finger_bone_tools(Operator):
    bl_idname = 'character_designer.finger_bone_tools'
    bl_label = 'Finger Bone Tools'
    bl_options = {'REGISTER', 'UNDO'}
    all_fingers: BoolProperty(default=False, options={'SKIP_SAVE'})
    action: EnumProperty(items=[(key, label, '') for key, label in (
        ('ALL', 'Calibrate All'), ('SELECTED', 'Calibrate Selected'),
        ('ALIGN_CHAIN', 'Align Active Finger'), ('RELAX_BONES', 'Relax Bones'),
        ('BEND', 'Preview Bend'), ('FLIP', 'Reverse Bend'))])

    @classmethod
    def description(cls, context, props):
        return {
            'BEND': 'Toggle cached bend axes for highlighted fingers on the preview side. Shift-click shows all captured fingers on that side; does not change pose',
            'ALIGN_CHAIN': 'Align the current finger between its existing root and tip, preserving Bone Roll. Thumb uses Relax instead',
            'RELAX_BONES': 'Relax selected Edit Mode bone chains along their existing curve, keeping root, tip and rolls. X Mirror synchronizes existing opposite chains from the active side. No finger capture is required',
            'FLIP': 'Reverse the saved bend direction of the current finger; does not move bones',
            'ALL': 'Calibrate Bone Roll for captured fingers on the current side using Basic Setup bend directions',
            'SELECTED': 'Calibrate selected finger bones on the current side using Basic Setup bend directions',
        }.get(props.action, 'Use the current Finger Basic Setup')

    def invoke(self, context, event):
        self.all_fingers = bool(event.shift)
        return self.execute(context)

    def execute(self, context):
        obj = bank.active_object(context)
        try:
            if self.action == 'RELAX_BONES':
                result = finger_chain.relax(context)
                message = f"Relaxed {result['changed']} bone segments in {result['chains']} selected/mirrored chains; root, tip and rolls kept."
                if result['skipped_mirrors']:
                    message += ' No complete opposite chain for: '+', '.join(result['skipped_mirrors'])+'.'
                if obj: obj.character_designer_finger_bank.bone_status = message
                self.report({'INFO'}, message)
                invalidate(context)
                return {'FINISHED'}
            if obj is None: raise ValueError('Capture Finger Basic Setup first.')
            state = obj.character_designer_finger_bank
            if self.action == 'BEND':
                if _intent == (obj, self.all_fingers): hide()
                else: show_bend(context, all_fingers=self.all_fingers)
                return {'FINISHED'}
            if self.action in {'ALL', 'SELECTED'}:
                started = time.perf_counter()
                result = targets.calibrate(context, self.action == 'SELECTED')
                lines = [f"Calibrated: {', '.join(result['success']) or 'none'} ({time.perf_counter()-started:.3f}s)"]
                lines += [f'{kind.title()} {key}: {message}' for kind in ('skipped', 'failed')
                          for key, message in result[kind].items()]
                state.bone_status = '\n'.join(lines)
                self.report({'INFO'} if not result['skipped'] and not result['failed'] else {'WARNING'}, ' | '.join(lines))
                invalidate(context)
                return {'FINISHED'} if result['success'] else {'CANCELLED'}
            if self.action == 'ALIGN_CHAIN':
                result = finger_chain.align(context)
                state.bone_status = f"Aligned {result['changed']} bone segments in {result['chains']} chains; root, tip and rolls kept."
                self.report({'INFO'}, state.bone_status)
            elif self.action == 'FLIP':
                slot = state.slots.get(state.active)
                if slot is None or not slot.guide.record: raise ValueError('Capture the active finger first.')
                flipped = not slot.guide.flip_bend
                slot.guide.flip_bend, slot.guide.confirmed = flipped, True
                state.bone_status = ''
                guides.redraw()
            invalidate(context)
            return {'FINISHED'}
        except _ERRORS as exc:
            if obj: obj.character_designer_finger_bank.bone_status = str(exc)
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw_controls(layout, context):
    obj = bank.active_object(context)
    armature_edit = context.object and context.object.type == 'ARMATURE' and context.mode == 'EDIT_ARMATURE'
    if obj is None and not armature_edit: return
    state = obj.character_designer_finger_bank if obj else None
    thumb = state and state.active.split('.')[0] == 'THUMB'
    box = layout.box()
    header = box.row(align=True)
    header.label(text='Bone Chain', icon='BONE_DATA')
    header.operator('character_designer.finger_bone_mirror', text='', icon='MOD_MIRROR')
    top = box.split(factor=.5, align=True)
    align = top.row(align=True)
    align.enabled = state is not None and not thumb
    align.operator('character_designer.finger_bone_tools', text='Align Finger').action = 'ALIGN_CHAIN'
    mark_cell = top.row(align=True)
    bottom = box.split(factor=.5, align=True)
    joint_cell = bottom.row(align=True)
    relax = bottom.row(align=True)
    relax.enabled = bool(armature_edit and any(b.select for b in context.object.data.edit_bones))
    relax.operator('character_designer.finger_bone_tools', text='Relax Bones').action = 'RELAX_BONES'
    from . import finger_loop_marks_ui
    finger_loop_marks_ui.draw_controls(mark_cell, joint_cell, context)
    if state is None: return
    box = layout.box()
    box.label(text='Bone Roll', icon='BONE_DATA')
    row = box.row(align=True)
    row.operator('character_designer.finger_bone_tools', text='Calibrate All').action = 'ALL'
    row.operator('character_designer.finger_bone_tools', text='Calibrate Selected').action = 'SELECTED'
    row = box.row(align=True)
    row.operator('character_designer.finger_bone_tools', text='Preview Bend',
                 depress=bool(_intent and _intent[0] == obj)).action = 'BEND'
    row.operator('character_designer.finger_bone_tools', text='', icon='ARROW_LEFTRIGHT').action = 'FLIP'
    if state.bone_status:
        for line in state.bone_status.splitlines():
            for part in textwrap.wrap(line, 45): layout.label(text=part)


@persistent
def _reset(*_args):
    hide()
    _saved_previews.clear()


@persistent
def _dirty(scene, depsgraph):
    """Legacy hook kept inert; editing never schedules a bend preview rebuild."""
    return None


CLASSES = (CHARACTERDESIGNER_OT_finger_bone_tools,)


def register_runtime():
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _reset not in handlers: handlers.append(_reset)


def unregister_runtime():
    _reset()
    for handle in _handles: bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handles.clear()
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _reset in handlers: handlers.remove(_reset)
    if _dirty in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_dirty)
