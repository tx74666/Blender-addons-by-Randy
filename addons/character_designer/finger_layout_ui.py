"""Two colored, surface-following joint rings with live numeric placement."""
import json
import time

import bmesh
import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Vector

from . import finger_layout as layout

COLORS = {'JOINT_1': (1., .28, .22, 1.), 'JOINT_2': (.1, .8, 1., 1.),
          'SUPPORT': (.62, .65, .7, .8), 'BETWEEN': (.85, .86, .9, .9)}
_handles = []
_preview = None


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()


def _update(_self, context):
    global _preview
    if _preview and context:
        try:
            plan = layout.build_layout(context)
            _preview['plan'] = plan
            _preview['error'] = ''
            layout.state(context).status = ''
        except (ValueError, ReferenceError) as exc:
            _preview['error'] = str(exc)
            layout.state(context).status = str(exc)
    _redraw()


class CharacterDesignerFingerLayoutState(PropertyGroup):
    mesh: PointerProperty(type=bpy.types.Object)
    source: PointerProperty(type=bpy.types.Mesh)
    record: StringProperty(options={'HIDDEN'})
    signature: StringProperty(options={'HIDDEN'})
    applied: BoolProperty(options={'HIDDEN'})
    joint_one: FloatProperty(name='Joint 1', description='Coral ring, root-to-tip fraction of the captured section', default=.34, min=.01, max=.99, subtype='FACTOR', update=_update)
    joint_two: FloatProperty(name='Joint 2', description='Cyan ring, root-to-tip fraction of the captured section', default=.68, min=.01, max=.99, subtype='FACTOR', update=_update)
    three_rings: BoolProperty(name='Three Rings per Joint', description='One center and one support ring on each side; reuse suitable nearby rings before adding', default=True, update=_update)
    width_one: FloatProperty(name='Width 1', description='Half width around Joint 1, fraction of the captured section', default=.025, min=.002, max=.2, precision=3, subtype='FACTOR', update=_update)
    width_two: FloatProperty(name='Width 2', description='Half width around Joint 2, fraction of the captured section', default=.025, min=.002, max=.2, precision=3, subtype='FACTOR', update=_update)
    between_rings: IntProperty(name='Between Joints', description='Additional rings between the two joint support regions; original shape rings remain', default=1, min=0, max=8, update=_update)
    slide_nearby: BoolProperty(name='Slide Nearby Rings', description='Pull suitable existing rings to the virtual targets; add only missing rings. Root/cap and seam boundaries stay fixed', default=True, update=_update)
    reverse: BoolProperty(name='Reverse Root / Tip', description='Swap the two fixed section boundaries; does not move geometry', update=_update)
    status: StringProperty(options={'SKIP_SAVE'})


def _stamp(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    return (len(bm.verts), len(bm.edges), len(bm.faces),
            hash(tuple(tuple(v.co) for v in bm.verts)),
            hash(tuple(tuple(v.index for v in f.verts) for f in bm.faces)))


def hide_preview():
    global _preview
    _preview = None
    _redraw()


def show_preview(context):
    global _preview
    plan = layout.build_layout(context)
    _preview = {'plan': plan, 'stamp': _stamp(plan['obj']), 'time': 0., 'error': ''}
    if not _handles and not bpy.app.background:
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_lines, (), 'WINDOW', 'POST_VIEW'))
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_labels, (), 'WINDOW', 'POST_PIXEL'))
    _redraw()


def _valid():
    if not _preview or _preview['error']: return None
    try:
        obj = _preview['plan']['obj']
        context = bpy.context
        if context.edit_object != obj or context.mode != 'EDIT_MESH' or not obj.visible_get(): return None
        now = time.monotonic()
        if now-_preview['time'] > .25:
            if _stamp(obj) != _preview['stamp']:
                _preview['error'] = 'Mesh changed. Recheck the finger before updating.'
                return None
            _preview['time'] = now
        return _preview['plan']
    except (ReferenceError, RuntimeError):
        hide_preview()
        return None


def _draw_lines():
    plan = _valid()
    if not plan: return
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    depth, width, blend = gpu.state.depth_test_get(), gpu.state.line_width_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('ALPHA')
        world = plan['obj'].matrix_world
        shader.bind()
        if 'root_point' in plan and not plan.get('defined'):
            # A dashed reference, deliberately not presented as real topology.
            center = sum(plan['root'], Vector())/len(plan['root'])
            root_ring = [p-center+plan['root_point'] for p in plan['root']]
            for ring in (root_ring, plan['root'], plan['tip']):
                segments = []
                for i, a in enumerate(ring):
                    b = ring[(i+1) % len(ring)]
                    for t in (0., .5): segments.extend((world @ a.lerp(b, t), world @ a.lerp(b, t+.25)))
                gpu.state.line_width_set(1.2)
                shader.uniform_float('color', (.7, .7, .7, .7))
                batch_for_shader(shader, 'LINES', {'pos': segments}).draw(shader)
            # Movement hints refer to the immutable original rings, not new loops.
            for i, t in plan.get('moves', {}).items():
                ring = next(r for r in plan['rings'] if r.get('reuse') == i)
                source = layout.state(bpy.context).source
                a = world @ source.vertices[plan['rows'][i][0]].co
                b = world @ ring['points'][0]
                shader.uniform_float('color', (.95, .7, .25, .8))
                batch_for_shader(shader, 'LINES', {'pos': [a, b]}).draw(shader)
        for ring in plan['rings']:
            points = [world @ p for p in ring['points']]
            segments = [p for i in range(len(points)) for p in (points[i], points[(i+1) % len(points)])]
            gpu.state.line_width_set(3.5 if ring['kind'].startswith('JOINT') else 1.2)
            shader.uniform_float('color', (1., .3, .05, .9) if ring['blocked'] else COLORS[ring['kind']])
            batch_for_shader(shader, 'LINES', {'pos': segments}).draw(shader)
    finally:
        gpu.state.depth_test_set(depth)
        gpu.state.line_width_set(width)
        gpu.state.blend_set(blend)


def _draw_labels():
    plan = _valid()
    if not plan: return
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    context = bpy.context
    if not context.region_data: return
    labels = [(r['points'][0], '1' if r['kind'] == 'JOINT_1' else '2', COLORS[r['kind']])
              for r in plan['rings'] if r['kind'].startswith('JOINT')]
    if 'root_point' in plan and not plan.get('defined'):
        labels += [(plan['root_point'], 'Root (virtual)', (.85, .85, .85, 1)),
                   (plan['tip_point'], 'Tip', (.85, .85, .85, 1))]
    elif not plan.get('defined'):
        labels += [(plan['root'][0], 'Root boundary', (.85, .85, .85, 1)),
                   (plan['tip'][0], 'Tip boundary', (.85, .85, .85, 1))]
    blf.size(0, 14)
    for point, text, color in labels:
        xy = location_3d_to_region_2d(context.region, context.region_data, plan['obj'].matrix_world @ point)
        if xy is not None:
            blf.position(0, xy.x+8, xy.y+6, 0)
            blf.color(0, *color)
            blf.draw(0, text)


class CHARACTERDESIGNER_OT_finger_layout(Operator):
    bl_idname = 'character_designer.finger_layout'
    bl_label = 'Finger Ring Layout'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=[(key, label, label) for key, label in (
        ('CAPTURE', 'Capture Finger Root'), ('PREPARE', 'Prepare Rings from Definition'), ('PREVIEW', 'Preview Joint Rings'),
        ('HIDE', 'Hide Preview'), ('APPLY', 'Generate / Update Rings'), ('CLEAR', 'Release Layout'))])

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        settings = layout.state(context)
        try:
            if self.action == 'PREPARE':
                hide_preview()
                layout.capture_definition(context)
                show_preview(context)
                settings.status = 'Editable body checked. The defined Start / End span is unchanged; no topology was modified.'
            elif self.action == 'CAPTURE':
                hide_preview()
                layout.capture(context)
                show_preview(context)
                settings.status = 'Root / Tip detected. Coral 1 and Cyan 2 are joint targets; the dashed root is only a reference.'
            elif self.action == 'PREVIEW':
                obj = layout._edit(context)
                if layout.fingerprint(obj) != settings.signature:
                    raise ValueError('The mesh or its data changed. Capture the finger again.')
                show_preview(context)
                settings.status = ''
            elif self.action == 'HIDE':
                hide_preview()
                settings.status = ''
            elif self.action == 'CLEAR':
                hide_preview()
                layout.clear(context)
                settings.status = ''
            else:
                added = layout.apply_layout(context)
                show_preview(context)
                moved = len(_preview['plan'].get('moves', {}))
                settings.status = f'Updated: {moved} rings moved, {added} vertices added. Root/cap protected; existing data transferred.'
            _redraw()
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, ReferenceError, TypeError) as exc:
            settings.status = str(exc)
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw_controls(layout_ui, context):
    from . import finger_definition as definition
    settings = layout.state(context)
    guide = definition.state(context)
    if not guide.record and not settings.source: return
    box = layout_ui.box()
    box.label(text='Finger Ring Layout', icon='MESH_GRID')
    row = box.row(align=True)
    prepare = row.row(align=True)
    prepare.enabled = bool(guide.record and guide.confirmed and guide.use_basis)
    prepare.operator('character_designer.finger_layout', text='Prepare Rings', icon='MESH_GRID').action = 'PREPARE'
    same_definition = not settings.record or not json.loads(settings.record).get('definition_id') or json.loads(settings.record)['definition_id'] == guide.revision
    if settings.source and same_definition:
        row.operator('character_designer.finger_layout', text='', icon='X').action = 'CLEAR'
        row = box.row(align=True)
        row.prop(settings, 'joint_one', slider=True)
        row.prop(settings, 'joint_two', slider=True)
        box.prop(settings, 'three_rings')
        if settings.three_rings:
            row = box.row(align=True)
            row.prop(settings, 'width_one')
            row.prop(settings, 'width_two')
        box.prop(settings, 'between_rings')
        data = json.loads(settings.record) if settings.record else {}
        if data.get('schema', 1) >= 2:
            box.prop(settings, 'slide_nearby')
            units = context.scene.unit_settings
            length = bpy.utils.units.to_string(units.system, 'LENGTH', data['length']*units.scale_length, precision=3)
            box.label(text=f'Finger length: {length}')
        else:
            box.prop(settings, 'reverse')
            box.label(text='Legacy layout; recapture for root detection.')
        row = box.row(align=True)
        row.operator('character_designer.finger_layout', text='Preview', icon='HIDE_OFF').action = 'PREVIEW'
        row.operator('character_designer.finger_layout', text='Hide', icon='HIDE_ON').action = 'HIDE'
        box.operator('character_designer.finger_layout', text='Generate / Update Rings', icon='MESH_GRID').action = 'APPLY'
        box.label(text='Root connection and fingertip stay fixed.')
    else:
        box.label(text='Prepare the active finger definition.')
        box.label(text='Topology is checked only when preparing.')
    if _preview and _preview.get('error'):
        import textwrap
        for line in textwrap.wrap(_preview['error'], width=43): box.label(text=line)
    if settings.status:
        import textwrap
        for line in textwrap.wrap(settings.status, width=45): box.label(text=line)


@persistent
def _invalidate(*_args):
    hide_preview()


CLASSES = (CharacterDesignerFingerLayoutState, CHARACTERDESIGNER_OT_finger_layout)


def register_runtime():
    bpy.types.Scene.character_designer_finger_layout = PointerProperty(type=CharacterDesignerFingerLayoutState)
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate not in handlers: handlers.append(_invalidate)


def unregister_runtime():
    hide_preview()
    for handle in _handles: bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handles.clear()
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate in handlers: handlers.remove(_invalidate)
    if hasattr(bpy.types.Scene, 'character_designer_finger_layout'):
        del bpy.types.Scene.character_designer_finger_layout
