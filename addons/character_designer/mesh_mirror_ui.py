"""Compact mesh mirror UI and non-destructive, numbered viewport preview."""

import time
import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Vector

from . import mesh_mirror as mirror
from .topology_symmetry import _select_result_region


_preview = None
_handlers = []
COLORS = ((1.0, .65, .12, 1), (.75, .25, 1.0, 1), (.12, .7, 1.0, 1),
          (1.0, .25, .5, 1), (.5, 1.0, .2, 1))


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _selection_signature(obj):
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    return (tuple((tuple(v.co), v.select, v.hide) for v in bm.verts),
            tuple((tuple(v.index for v in f.verts), f.select, f.hide) for f in bm.faces))


def clear_preview():
    global _preview
    _preview = None
    _redraw()


@persistent
def _invalidate_preview(*args):
    clear_preview()


def _valid_preview(force=False):
    if _preview is None:
        return None
    try:
        plan = _preview['plan']
        if (bpy.context.edit_object != plan.obj or plan.obj.mode != 'EDIT'
                or mirror._matrix_tuple(plan.obj.matrix_world) != _preview['world']
                or (plan.reference and mirror._matrix_tuple(plan.reference.matrix_world) != plan.reference_matrix)):
            clear_preview()
            return None
        now = time.monotonic()
        # Check the edit buffer at most ~7 times/s, not twice per draw pass.
        # The operator always rebuilds and validates an exact fresh plan.
        if force or now - _preview['last_check'] >= .15:
            _preview['last_check'] = now
            if _selection_signature(plan.obj) != _preview['selection']:
                clear_preview()
                return None
        return _preview
    except (ReferenceError, RuntimeError):
        clear_preview()
        return None


def preview_geometry(plan, chosen=0):
    """CPU payload also exercised in headless tests; no mesh writes."""
    obj = plan.obj
    mesh, world = obj.data, obj.matrix_world
    batches, labels = [], []
    def wire(faces, color, transform=None):
        edges = {mesh.loops[i].edge_index for fi in faces for i in mesh.polygons[fi].loop_indices}
        coordinates = []
        for index in edges:
            for vi in mesh.edges[index].vertices:
                co = mesh.vertices[vi].co
                coordinates.append(tuple(world @ (transform @ co if transform is not None else co)))
        batches.append((color, coordinates))
    wire(plan.source_faces, (.15, .75, 1, 1))
    wire(plan.source_faces, (.15, 1, .4, 1), plan.reflection)
    source = [world @ mesh.vertices[i].co for i in plan.source_vertices]
    result = [world @ (plan.reflection @ mesh.vertices[i].co) for i in plan.source_vertices]
    source_center = sum(source, Vector()) / len(source)
    result_center = sum(result, Vector()) / len(result)
    labels.extend([(tuple(source_center), 'Source: unchanged', (.15, .75, 1, 1)),
                   (tuple(result_center), 'Mirrored result', (.15, 1, .4, 1))])
    if not chosen and plan.target_vertices:
        chosen = next((i for i,c in enumerate(plan.candidates, 1) if c.vertices == plan.target_vertices), 0)
    for number, candidate in enumerate(plan.candidates, 1):
        if chosen and not plan.needs_choice and number != chosen:
            continue
        color = COLORS[(number - 1) % len(COLORS)]
        if chosen and number != chosen:
            color = tuple(v * .4 for v in color[:3]) + (1,)
        wire(candidate.faces, color)
        loose_lines = [tuple(world @ mesh.vertices[vi].co) for ei in candidate.edges
                       if mesh.edges[ei].is_loose for vi in mesh.edges[ei].vertices]
        if loose_lines:
            batches.append((color, loose_lines))
        scope = 'entire strand' if plan.region_kind == 'ISLAND' else 'inside boundary'
        labels.append((tuple(world @ Vector(candidate.center)), f'Target {number}: replace {scope}', color))
    _, local_normal, offset = mirror.mirror_frame(obj, plan.reference)
    normal = (world.to_3x3().inverted().transposed() @ local_normal).normalized()
    center = (source_center + result_center) * .5
    # Default local-X and reference-X both pass through the midpoint of each
    # source/result pair, even when the mesh object has nonuniform transforms.
    u = normal.orthogonal().normalized()
    v = normal.cross(u).normalized()
    radius = max(max((point-center).length for point in source+result) * 1.1, .001)
    corners = [center + radius*(a*u+b*v) for a,b in ((-1,-1), (1,-1), (1,1), (-1,1))]
    batches.append(((.8, .8, .8, 1), [tuple(corners[i]) for pair in ((0,1),(1,2),(2,3),(3,0)) for i in pair]))
    plane_label = 'Mirror plane'
    labels.append((tuple(corners[2]), plane_label, (.9, .9, .9, 1)))
    return batches, labels


def show_preview(plan, chosen=0):
    global _preview
    batches, labels = preview_geometry(plan, chosen)
    _preview = {'plan': plan, 'batches': batches, 'labels': labels,
                'world': mirror._matrix_tuple(plan.obj.matrix_world),
                'selection': _selection_signature(plan.obj), 'last_check': 0.0}
    _redraw()


def _draw_lines():
    data = _valid_preview()
    if not data:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    old_depth, old_width = gpu.state.depth_test_get(), gpu.state.line_width_get()
    try:
        gpu.state.depth_test_set('NONE')
        gpu.state.line_width_set(2)
        if 'gpu_batches' not in data:
            data['gpu_batches'] = [(color, batch_for_shader(shader, 'LINES', {'pos': positions}))
                                   for color, positions in data['batches'] if positions]
        for color, batch in data['gpu_batches']:
            shader.bind()
            shader.uniform_float('color', color)
            batch.draw(shader)
    finally:
        gpu.state.depth_test_set(old_depth)
        gpu.state.line_width_set(old_width)


def _draw_labels():
    data = _valid_preview()
    if not data:
        return
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    context = bpy.context
    if not context.region_data:
        return
    blf.size(0, 13)
    occupied = []
    for position, label, color in data['labels']:
        screen = location_3d_to_region_2d(context.region, context.region_data, Vector(position))
        if screen is not None:
            x, y = screen.x + 7, screen.y + 7
            width = blf.dimensions(0, label)[0]
            while any(x < right and x + width > left and abs(y - row) < 17 for left, right, row in occupied):
                y += 17
            occupied.append((x, x + width, y))
            blf.position(0, x, y, 0)
            blf.color(0, *color)
            blf.draw(0, label)


class CharacterDesignerMeshMirrorSettings(PropertyGroup):
    reference: PointerProperty(
        name='Symmetry Reference', type=bpy.types.Object,
        description='Optional object defining its X=0 mirror plane; leave empty for Mesh Local X=0. No rig required')
    tolerance: FloatProperty(
        name='Seam Tolerance', default=0, min=0, precision=5,
        description='Mesh-local distance for attachment matching; 0 uses 10% of the median selected edge length. Not a global merge distance')


def _plan(context, candidate=0):
    settings = context.scene.character_designer_mesh_mirror
    return mirror.build_plan(context, reference=settings.reference,
                             tolerance=settings.tolerance, target_candidate=candidate)


class CHARACTERDESIGNER_OT_mesh_mirror_settings(Operator):
    bl_idname = 'character_designer.mesh_mirror_settings'
    bl_label = 'Mirror Settings'
    bl_description = 'Optional mirror reference and boundary tolerance'
    bl_options = {'REGISTER'}

    reference_name: StringProperty(name='Symmetry Reference')
    tolerance: FloatProperty(name='Seam Tolerance (0 = Auto)', min=0, default=0, precision=5)

    def invoke(self, context, event):
        settings = context.scene.character_designer_mesh_mirror
        self.reference_name = settings.reference.name if settings.reference else ''
        self.tolerance = settings.tolerance
        return context.window_manager.invoke_props_dialog(self, width=390)

    def draw(self, context):
        self.layout.label(text='Default plane: this mesh\'s local X = 0')
        self.layout.prop_search(self, 'reference_name', bpy.data, 'objects')
        self.layout.prop(self, 'tolerance')
        self.layout.label(text='Reference X=0; no binding or Apply Transform.')
        self.layout.label(text='Weights: .L / .R swapped; missing groups created.')
        self.layout.label(text='Other group names and UV coordinates are copied.')

    def execute(self, context):
        settings = context.scene.character_designer_mesh_mirror
        reference = bpy.data.objects.get(self.reference_name) if self.reference_name else None
        if self.reference_name and reference is None:
            self.report({'ERROR'}, 'The reference object no longer exists.')
            return {'CANCELLED'}
        settings.reference, settings.tolerance = reference, self.tolerance
        clear_preview()
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_mesh_mirror_preview(Operator):
    bl_idname = 'character_designer.mesh_mirror_preview'
    bl_label = 'Preview Replacement'
    bl_description = 'Read-only: cyan source, green result, grey plane, numbered target candidates. Click again to clear'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        if _valid_preview(force=True):
            clear_preview()
            return {'FINISHED'}
        try:
            plan = _plan(context)
            show_preview(plan)
            if plan.needs_choice:
                self.report({'WARNING'}, f'{len(plan.candidates)} possible targets. Click Mirror Selected Region and choose a numbered target.')
            else:
                action = ('Replace entire opposite strand' if plan.region_kind == 'ISLAND' else 'Replace region inside boundary') if plan.target_vertices else 'Create missing opposite region'
                self.report({'INFO'}, action + '; cyan source stays unchanged. This preview does not modify geometry.')
            return {'FINISHED'}
        except mirror.MirrorError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_mesh_mirror(Operator):
    bl_idname = 'character_designer.mirror_selected_region'
    bl_label = 'Mirror Selected Region'
    bl_description = ('Mirror selected geometry and existing weights using the existing skeleton. '
                      'A whole strand replaces ONE opposite strand; partial selections replace the region bounded by their edge loops')
    bl_options = {'REGISTER', 'UNDO'}

    target_candidate: IntProperty(name='Target Candidate', default=0, min=0,
                                  description='0: automatic. Otherwise enter a numbered viewport candidate; never append over ambiguous geometry')
    sync_fingers: BoolProperty(name='Sync Finger References', default=False,
                              description='After mirroring, sync proven references and loop marks for fully selected fingers; keep partial or unavailable saved setups and all bones unchanged')

    @classmethod
    def description(cls, context, properties):
        if properties.sync_fingers:
            return ('Mirror the selected mesh region and its weights, then update matching finger guides and yellow marks. '
                    'For L/R differ: select the edited region first. Partial references stay saved; bones have a separate mirror action')
        return cls.bl_description

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def invoke(self, context, event):
        self.target_candidate = 0
        try:
            plan = _plan(context)
            if plan.needs_choice:
                self._choice_plan = plan
                show_preview(plan)
                return context.window_manager.invoke_props_dialog(self, width=430, confirm_text='Replace Chosen Target')
        except mirror.MirrorError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return self.execute(context)

    def draw(self, context):
        if not hasattr(self, '_choice_plan'):
            self.layout.prop(self, 'target_candidate')
            return
        self.layout.label(text='Target is not certain. Nothing has been changed.', icon='ERROR')
        self.layout.label(text='Inspect the numbered outlines in the viewport.')
        for i, candidate in enumerate(self._choice_plan.candidates, 1):
            self.layout.label(text=f'{i}: {len(candidate.faces)} faces · surface coverage {candidate.coverage[0]:.0%} / {candidate.coverage[1]:.0%}')
        self.layout.prop(self, 'target_candidate')
        self.layout.label(text='0 will not execute while the target is ambiguous.')

    def check(self, context):
        if hasattr(self, '_choice_plan'):
            show_preview(self._choice_plan, self.target_candidate)
        return True

    def execute(self, context):
        obj = context.edit_object
        did_commit = False
        try:
            plan = _plan(context, self.target_candidate)
            if hasattr(self, '_choice_plan'):
                pending = self._choice_plan
                if (plan.fingerprint != pending.fingerprint or plan.source_faces != pending.source_faces
                        or plan.reference != pending.reference or plan.reference_matrix != pending.reference_matrix
                        or tuple(c.vertices for c in plan.candidates) != tuple(c.vertices for c in pending.candidates)):
                    raise mirror.MirrorError('The mesh, selection or mirror plane changed during target selection; preview again.')
            if plan.needs_choice:
                show_preview(plan)
                raise mirror.MirrorError('Choose a numbered target candidate first; no geometry was changed.')
            sync_result = None
            if self.sync_fingers:
                from . import finger_mirror_sync
                packet = finger_mirror_sync.prepare(plan)
                metadata = finger_mirror_sync.snapshot(obj)
            def committed(selection):
                nonlocal sync_result
                try:
                    if self.sync_fingers:
                        sync_result = finger_mirror_sync.apply(context, plan, packet)
                    _select_result_region(obj, selection)
                except Exception:
                    if self.sync_fingers: finger_mirror_sync.restore(obj, metadata)
                    raise
            bpy.ops.object.mode_set(mode='OBJECT')
            mirror.apply_plan(plan, after_commit=committed)
            did_commit = True
            if self.sync_fingers:
                summary = f"Mirrored; synced {sync_result['synced']} finger(s), {sync_result['unchanged']} left unchanged"
                state = obj.character_designer_finger_bank
                state.status, state.bone_status = '', summary
            # Geometry and metadata are already committed. A display-only
            # refresh failure must not cancel Undo or claim the mesh rolled back.
            try:
                clear_preview()
                if self.sync_fingers:
                    from . import finger_definition_ui, finger_loop_marks_ui, finger_bone_tools
                    finger_definition_ui.redraw()
                    finger_loop_marks_ui.refresh(context)
                    finger_bone_tools.invalidate(context)
            except Exception:
                import traceback
                traceback.print_exc()
            if self.sync_fingers:
                self.report({'INFO'}, summary)
                return {'FINISHED'}
            action = (f'Replaced entire opposite strand ({len(plan.target_faces)} faces)' if plan.region_kind == 'ISLAND'
                      else f'Replaced {len(plan.target_faces)} faces inside boundary') if plan.target_vertices else 'Created missing opposite region'
            self.report({'INFO'}, f'{action}; source unchanged. Mirrored {len(plan.source_faces)} faces.')
            return {'FINISHED'}
        except mirror.MirrorError as error:
            if did_commit:
                self.report({'WARNING'}, f'Mirror completed; display refresh failed: {error}')
                return {'FINISHED'}
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        except Exception as error:
            import traceback
            traceback.print_exc()
            if did_commit:
                self.report({'WARNING'}, f'Mirror completed; display refresh failed: {error}')
                return {'FINISHED'}
            self.report({'ERROR'}, f'Mirror failed; original mesh restored: {error}')
            return {'CANCELLED'}
        finally:
            if obj and obj.mode == 'OBJECT':
                bpy.ops.object.mode_set(mode='EDIT')


MESH_MIRROR_CLASSES = (CharacterDesignerMeshMirrorSettings,
                       CHARACTERDESIGNER_OT_mesh_mirror_settings,
                       CHARACTERDESIGNER_OT_mesh_mirror_preview,
                       CHARACTERDESIGNER_OT_mesh_mirror)


def register_mesh_mirror_runtime():
    if not hasattr(bpy.types.Scene, 'character_designer_mesh_mirror'):
        bpy.types.Scene.character_designer_mesh_mirror = PointerProperty(type=CharacterDesignerMeshMirrorSettings)
    if not _handlers and not bpy.app.background:
        _handlers.extend((bpy.types.SpaceView3D.draw_handler_add(_draw_lines, (), 'WINDOW', 'POST_VIEW'),
                          bpy.types.SpaceView3D.draw_handler_add(_draw_labels, (), 'WINDOW', 'POST_PIXEL')))
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate_preview not in handlers:
            handlers.append(_invalidate_preview)


def unregister_mesh_mirror_runtime():
    clear_preview()
    for handle in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handlers.clear()
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate_preview in handlers:
            handlers.remove(_invalidate_preview)
    if hasattr(bpy.types.Scene, 'character_designer_mesh_mirror'):
        del bpy.types.Scene.character_designer_mesh_mirror
