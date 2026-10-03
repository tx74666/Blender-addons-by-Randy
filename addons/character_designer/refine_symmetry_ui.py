"""Coordinate-only symmetry repair with read-only analysis and preview."""

import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Vector

from . import refine_symmetry as repair
from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_MODELING, UI_PAGE_WEIGHT, active_ui_page


_analysis = None
_preview = None
_handlers = []


def _mesh(context):
    obj = context.edit_object or context.active_object
    return obj if obj is not None and obj.type == 'MESH' else None


def _redraw():
    manager = getattr(bpy.context, 'window_manager', None)
    for window in getattr(manager, 'windows', ()):
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _signature(obj):
    """Include all Shape Keys, live edit layers, and protected mesh state."""
    return repair.fingerprint(obj)


def _settings_signature(settings):
    return (settings.axis, settings.mode, settings.selected_only,
            settings.tolerance, settings.centerline_tolerance)


def clear_preview():
    global _preview
    _preview = None
    _redraw()


def clear_analysis():
    global _analysis
    _analysis = None
    clear_preview()


@persistent
def _invalidate(*args):
    clear_analysis()


def _settings_changed(self, context):
    clear_analysis()


def _cached(context, cache, force=False):
    if cache is None:
        return None
    try:
        obj = cache['obj']
        settings = context.scene.character_designer_refine_symmetry
        if (_mesh(context) != obj or obj.mode not in {'OBJECT', 'EDIT'}
                or _settings_signature(settings) != cache['settings']):
            return None
        now = time.monotonic()
        if force or now - cache['last_check'] >= .2:
            cache['last_check'] = now
            if _signature(obj) != cache['signature']:
                return None
        return cache
    except (AttributeError, ReferenceError, RuntimeError, repair.RefineSymmetryError):
        return None


def _valid_analysis(context, force=False):
    global _analysis
    if _analysis is not None and _cached(context, _analysis, force) is None:
        _analysis = None
    return _analysis


def _valid_preview(force=False):
    global _preview
    if _preview is not None:
        valid = _cached(bpy.context, _preview, force)
        try:
            valid = valid and _world_signature(_preview['obj']) == _preview['world']
        except (ReferenceError, RuntimeError):
            valid = False
        if not valid:
            _preview = None
            return None
    return _preview


def _packet(context, obj, fingerprint=None):
    return {'obj': obj, 'signature': fingerprint or _signature(obj),
            'settings': _settings_signature(context.scene.character_designer_refine_symmetry),
            'last_check': time.monotonic()}


def _world_signature(obj):
    return tuple(tuple(row) for row in obj.matrix_world)


def _remember(context, obj, result):
    global _analysis
    _analysis = _packet(context, obj, result.fingerprint)
    _analysis['result'] = result


def _options(context):
    settings = context.scene.character_designer_refine_symmetry
    return {'axis': settings.axis,
            'selected_only': settings.selected_only,
            'tolerance': settings.tolerance,
            'centerline_tolerance': settings.centerline_tolerance}


def _analyze(context):
    obj = _mesh(context)
    if obj is None:
        raise repair.RefineSymmetryError('Make a mesh active first.')
    return obj, repair.analyze(obj, **_options(context))


def _plan(context):
    obj = _mesh(context)
    if obj is None:
        raise repair.RefineSymmetryError('Make a mesh active first.')
    settings = context.scene.character_designer_refine_symmetry
    return repair.plan(obj, mode=settings.mode, **_options(context))


def _summary(result):
    return (f'Matched {result.matched} pairs; Misaligned {result.misaligned}; '
            f'Unmatched {len(result.unmatched)} vertices; max error {result.max_error:.6g}')


def preview_geometry(plan):
    """Return original/target coordinates without writing a mesh or Shape Key."""
    obj = plan.obj
    world = obj.matrix_world
    basis = plan._before['basis']
    originals, targets, movement = [], [], []
    for index, position in sorted(plan.positions.items()):
        old = Vector(basis[index * 3:index * 3 + 3])
        start, end = tuple(world @ old), tuple(world @ Vector(position))
        originals.append(start)
        targets.append(end)
        if start != end:
            movement.extend((start, end))
    return [('POINTS', (1.0, .55, .12, 1.0), originals),
            ('POINTS', (.12, 1.0, .45, 1.0), targets),
            ('LINES', (.12, 1.0, .45, 1.0), movement)]


def show_preview(plan, context=None):
    global _preview
    context = context or bpy.context
    _preview = _packet(context, plan.obj, plan.analysis.fingerprint)
    _preview.update(plan=plan, batches=preview_geometry(plan),
                    world=_world_signature(plan.obj))
    _remember(context, plan.obj, plan.analysis)
    _redraw()


def _draw_geometry():
    data = _valid_preview()
    if not data:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    old_depth, old_width = gpu.state.depth_test_get(), gpu.state.line_width_get()
    try:
        gpu.state.depth_test_set('NONE')
        gpu.state.line_width_set(2)
        if 'gpu_shader' not in data:
            data['gpu_shader'] = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader = data['gpu_shader']
        if 'gpu_batches' not in data:
            data['gpu_batches'] = [(color, batch_for_shader(shader, kind, {'pos': positions}))
                                   for kind, color, positions in data['batches'] if positions]
        for color, batch in data['gpu_batches']:
            shader.bind()
            shader.uniform_float('color', color)
            batch.draw(shader)
    except Exception:
        clear_preview()
    finally:
        gpu.state.depth_test_set(old_depth)
        gpu.state.line_width_set(old_width)


def _draw_label():
    if not _valid_preview():
        return
    import blf
    blf.size(0, 13)
    blf.position(0, 22, 48, 0)
    blf.color(0, .12, 1.0, .45, 1.0)
    blf.draw(0, 'Refine preview: orange = current; green = proposed')


class CharacterDesignerRefineSymmetrySettings(PropertyGroup):
    axis: EnumProperty(
        name='Axis', default='X', update=_settings_changed,
        items=(('X', 'X', 'Mirror across local X=0'),
               ('Y', 'Y', 'Mirror across local Y=0'),
               ('Z', 'Z', 'Mirror across local Z=0')))
    mode: EnumProperty(
        name='Repair Mode', default='LEFT_TO_RIGHT', update=_settings_changed,
        items=(('LEFT_TO_RIGHT', 'Left \u2192 Right', 'Copy the positive axis side to the negative side; only selected destination vertices can change'),
               ('RIGHT_TO_LEFT', 'Right \u2192 Left', 'Copy the negative axis side to the positive side; only selected destination vertices can change'),
               ('AVERAGE', 'Average', 'Mirror-space average; both vertices of each affected pair must be selected')))
    selected_only: BoolProperty(
        name='Selected Region Only', default=True, update=_settings_changed,
        description='Only selected, visible vertices may move. Average requires both sides selected; directional modes require the destination selected')
    tolerance: FloatProperty(
        name='Mirror Tolerance', default=1.0e-6, min=1.0e-9, max=1.999e-5,
        precision=7, update=_settings_changed,
        description='Mesh-local distance used to report geometric misalignment and validate coordinate-based mirror matching')
    centerline_tolerance: FloatProperty(
        name='Centerline Tolerance', default=1.0e-6, min=0.0, max=1.999e-5,
        precision=7, update=_settings_changed,
        description='Only topology-proven center vertices this close to the selected local mirror plane may be snapped to zero')
    show_advanced: BoolProperty(name='Advanced', default=False)


class _MeshOperator:
    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'EDIT_MESH'} and _mesh(context) is not None


class CHARACTERDESIGNER_OT_analyze_refine_symmetry(_MeshOperator, Operator):
    bl_idname = 'character_designer.analyze_refine_symmetry'
    bl_label = 'Analyze'
    bl_description = 'Read-only: inspect reliable topology pairs and their Basis geometry error; no nearest-vertex fallback'
    bl_options = {'REGISTER'}

    def execute(self, context):
        clear_analysis()
        try:
            obj, result = _analyze(context)
            _remember(context, obj, result)
            self.report({'INFO'}, _summary(result))
            return {'FINISHED'}
        except repair.RefineSymmetryError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_select_refine_misaligned(_MeshOperator, Operator):
    bl_idname = 'character_designer.select_refine_misaligned'
    bl_label = 'Select Misaligned'
    bl_description = 'Select currently misaligned target vertices without changing coordinates, topology, weights, or Shape Keys'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            obj, result = _analyze(context)
            selected = set(result.misaligned_vertices)
            if obj.mode == 'EDIT':
                import bmesh
                bm = bmesh.from_edit_mesh(obj.data)
                for face in bm.faces:
                    face.select_set(False)
                for edge in bm.edges:
                    edge.select_set(False)
                for index, vertex in enumerate(bm.verts):
                    vertex.select_set(index in selected and not vertex.hide)
                # Visible selected vertices remain isolated in Vertex selection mode.
                context.tool_settings.mesh_select_mode = (True, False, False)
                bm.select_history.clear()
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            else:
                for polygon in obj.data.polygons:
                    polygon.select = False
                for edge in obj.data.edges:
                    edge.select = False
                for vertex in obj.data.vertices:
                    vertex.select = vertex.index in selected and not vertex.hide
                obj.data.update()
            # Selection changes the target scope, so the previous report cannot
            # be cached as if it described the new selected region.
            clear_analysis()
            self.report({'INFO'}, f'Selected {len(selected)} misaligned vertices; coordinates unchanged.')
            return {'FINISHED'}
        except repair.RefineSymmetryError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_preview_refine_symmetry(_MeshOperator, Operator):
    bl_idname = 'character_designer.preview_refine_symmetry'
    bl_label = 'Preview'
    bl_description = 'Read-only overlay: orange current vertices, green proposed coordinates; click again to clear'
    bl_options = {'REGISTER'}

    def execute(self, context):
        if _valid_preview(force=True):
            clear_preview()
            return {'FINISHED'}
        try:
            plan = _plan(context)
            show_preview(plan, context)
            unmatched = len(plan.analysis.unmatched)
            self.report({'WARNING'} if unmatched else {'INFO'},
                        f'Previewing {len(plan.positions)} vertices; Unmatched {unmatched}; '
                        'mesh and Shape Keys unchanged.')
            return {'FINISHED'}
        except repair.RefineSymmetryError as error:
            clear_preview()
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_refine_symmetry(_MeshOperator, Operator):
    bl_idname = 'character_designer.refine_symmetry'
    bl_label = 'Refine Symmetry'
    bl_description = 'Repair Basis coordinates with reliable topology pairs; preserve every Shape Key deformation delta; validate and roll back on failure'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            # Always rebuild from current geometry, selection and settings.
            plan = _plan(context)
            result = repair.apply(plan.obj, plan)
        except repair.RefineSymmetryError as error:
            clear_analysis()
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        except Exception as error:
            import traceback
            traceback.print_exc()
            clear_analysis()
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        # UI refresh cannot invalidate a successful, undoable coordinate repair.
        try:
            clear_preview()
            _remember(context, plan.obj, result)
            _redraw()
        except Exception:
            import traceback
            traceback.print_exc()
        ready = getattr(result, 'ordinary_mirror_ready', False)
        unmatched = len(result.unmatched)
        axis = context.scene.character_designer_refine_symmetry.axis
        validation = (f'{axis} mirror target geometry validated' if ready
                      else 'inspect the validation report before using coordinate-based mirror matching')
        message = (f'Refined {len(plan.positions)} vertices; {validation}. '
                   f'Unmatched {unmatched} vertices left unchanged. '
                   'Shape Key deformation deltas preserved. Ctrl+Z to undo.')
        self.report({'INFO'} if ready and not unmatched else {'WARNING'}, message)
        return {'FINISHED'}


def draw_refine_symmetry(layout, context):
    settings = context.scene.character_designer_refine_symmetry
    row = layout.row(align=True)
    row.prop(settings, 'axis', expand=True)
    layout.prop(settings, 'mode')
    layout.prop(settings, 'selected_only')
    if settings.selected_only:
        if settings.mode == 'AVERAGE':
            layout.label(text='Select both vertices of each pair.')
        else:
            layout.label(text='Select the destination side to repair.')
    row = layout.row(align=True)
    row.operator('character_designer.preview_refine_symmetry',
                 text='Clear Preview' if _valid_preview() else 'Preview', icon='HIDE_OFF')
    row.operator('character_designer.refine_symmetry', icon='MOD_MIRROR')
    cache = _valid_analysis(context)
    if cache and cache['result'].unmatched:
        layout.label(text=f"Unmatched: {len(cache['result'].unmatched)} (unchanged)", icon='ERROR')
    layout.prop(settings, 'show_advanced',
                icon='TRIA_DOWN' if settings.show_advanced else 'TRIA_RIGHT',
                emboss=False)
    if not settings.show_advanced:
        return
    advanced = layout.box()
    row = advanced.row(align=True)
    row.operator('character_designer.analyze_refine_symmetry', icon='VIEWZOOM')
    row.operator('character_designer.select_refine_misaligned', icon='RESTRICT_SELECT_OFF')
    if cache:
        result = cache['result']
        box = advanced.box()
        box.label(text=f'Matched: {result.matched} pairs')
        box.label(text=f'Misaligned: {result.misaligned}')
        box.label(text=f'Unmatched: {len(result.unmatched)} vertices')
        box.label(text=f'Centerline: {len(result.centerline)} vertices')
        box.label(text=f'Max Error: {result.max_error:.6g}')
        if getattr(result, 'ordinary_mirror_ready', False):
            box.label(text=f'{settings.axis} Mirror: repaired pairs validated', icon='CHECKMARK')
            box.label(text=f'Validated Pairs: {result.validated_pairs}')
    advanced.prop(settings, 'tolerance')
    advanced.prop(settings, 'centerline_tolerance')
    advanced.label(text=f'Local {settings.axis}=0; Left = +{settings.axis}, Right = -{settings.axis}.')
    advanced.label(text='Shape Keys preserved. Undo: Ctrl+Z.')


class CHARACTERDESIGNER_PT_refine_symmetry(Panel):
    bl_label = 'Refine Symmetry'
    bl_idname = 'CHARACTERDESIGNER_PT_refine_symmetry'
    bl_parent_id = 'CHARACTERDESIGNER_PT_main'
    bl_order = 4
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = SIDEBAR_CATEGORY
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) in {UI_PAGE_MODELING, UI_PAGE_WEIGHT}

    def draw(self, context):
        draw_refine_symmetry(self.layout, context)


REFINE_SYMMETRY_CLASSES = (
    CharacterDesignerRefineSymmetrySettings,
    CHARACTERDESIGNER_OT_analyze_refine_symmetry,
    CHARACTERDESIGNER_OT_select_refine_misaligned,
    CHARACTERDESIGNER_OT_preview_refine_symmetry,
    CHARACTERDESIGNER_OT_refine_symmetry,
    CHARACTERDESIGNER_PT_refine_symmetry,
)


def register_refine_symmetry_runtime():
    if not hasattr(bpy.types.Scene, 'character_designer_refine_symmetry'):
        bpy.types.Scene.character_designer_refine_symmetry = PointerProperty(type=CharacterDesignerRefineSymmetrySettings)
    if not _handlers and not bpy.app.background:
        _handlers.extend((bpy.types.SpaceView3D.draw_handler_add(_draw_geometry, (), 'WINDOW', 'POST_VIEW'),
                          bpy.types.SpaceView3D.draw_handler_add(_draw_label, (), 'WINDOW', 'POST_PIXEL')))
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate not in handlers:
            handlers.append(_invalidate)


def unregister_refine_symmetry_runtime():
    clear_analysis()
    for handle in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handlers.clear()
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate in handlers:
            handlers.remove(_invalidate)
    if hasattr(bpy.types.Scene, 'character_designer_refine_symmetry'):
        del bpy.types.Scene.character_designer_refine_symmetry
