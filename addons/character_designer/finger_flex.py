"""Artist-defined finger flexion from a mesh face normal and longitudinal edge.

The saved guide is mesh-local. Positive local-X rotation must move the bone
toward the orange arrow: A = T cross B, so A cross T = B.
"""

import json
import math

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Matrix, Quaternion, Vector

EPS = 1e-8
_handle = None
_visible = False


def _redraw(_self, _context):
    from .finger_bones import _tag_redraw
    _tag_redraw()


class CharacterDesignerFingerFlexState(PropertyGroup):
    mesh: PointerProperty(type=bpy.types.Object)
    guide: StringProperty(options={'HIDDEN'})
    flip_forward: BoolProperty(name='Reverse Tip Arrow', update=_redraw)
    flip_bend: BoolProperty(name='Reverse Bend Arrow', update=_redraw)
    show_legacy: BoolProperty(name='Existing Bone Roll Tools', options={'SKIP_SAVE'})
    status: StringProperty(options={'SKIP_SAVE'})


def state(context):
    return context.scene.character_designer_finger_flex


def frame(tangent, normal):
    tangent, normal = Vector(tangent), Vector(normal)
    if min(tangent.length, normal.length) < EPS:
        raise ValueError('The guide has a zero-length direction.')
    tangent.normalize()
    bend = normal - tangent * normal.dot(tangent)
    if bend.length < normal.length * 1e-4:
        raise ValueError('The normal is parallel to the finger; choose a side face.')
    bend.normalize()
    return tangent, bend, tangent.cross(bend).normalized()


def _strip_geometry(faces):
    """Accept one non-branching quad row; shared cross-edges define its length."""
    if any(len(f.verts) != 4 for f in faces):
        raise ValueError('Select one continuous row of top quads along one finger.')
    selected = set(faces)
    neighbors = {f: [(e, other) for e in f.edges for other in e.link_faces
                     if other in selected and other != f] for f in faces}
    ends = [f for f in faces if len(neighbors[f]) == 1]
    if len(ends) != 2 or any(len(n) not in (1, 2) for n in neighbors.values()):
        raise ValueError('Select one continuous top strip, without branches or closed loops.')
    ordered, previous, current = [], None, min(ends, key=lambda f: f.index)
    while current is not None:
        if current in ordered:
            raise ValueError('The top strip contains a loop.')
        ordered.append(current)
        choices = [f for _, f in neighbors[current] if f != previous]
        previous, current = current, choices[0] if choices else None
    if len(ordered) != len(faces):
        raise ValueError('The top strip is disconnected.')
    for f in ordered[1:-1]:
        edges = [e for e, _ in neighbors[f]]
        if set(edges[0].verts) & set(edges[1].verts):
            raise ValueError('The selection turns across the finger. Select a single lengthwise quad row.')

    def end_midpoint(face):
        shared = neighbors[face][0][0]
        opposite = next(e for e in face.edges if not set(e.verts) & set(shared.verts))
        return (opposite.verts[0].co + opposite.verts[1].co) * .5

    start, end = end_midpoint(ordered[0]), end_midpoint(ordered[-1])
    normal = sum((f.normal * f.calc_area() for f in ordered), Vector())
    if normal.length < EPS or any(f.normal.dot(normal.normalized()) < .25 for f in ordered):
        raise ValueError('The selected faces wrap around the finger or have inconsistent normals. Select only the top row.')
    return end - start, normal.normalized(), (start + end) * .5


def capture(context):
    obj = context.edit_object
    if context.mode != 'EDIT_MESH' or obj is None:
        raise ValueError('Enter Mesh Edit Mode and select a top face strip or one longitudinal edge.')
    if len(context.objects_in_mode_unique_data) != 1:
        raise ValueError('Edit one mesh at a time to define the bend direction.')
    if obj.active_shape_key_index > 0:
        raise ValueError('Select the Basis shape key before defining rest-pose bend axes.')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.normal_update()
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    faces = [f for f in bm.faces if f.select and not f.hide]
    edges = [e for e in bm.edges if e.select and not e.hide]
    if len(faces) > 1:
        tangent, normal, origin = _strip_geometry(faces)
    elif len(faces) == 1:
        face = faces[0]
        if len(face.verts) != 4:
            raise ValueError('For automatic length direction select a quad, or select one edge on this face.')
        loops = list(face.loops)
        candidates = []
        for i in (0, 1):
            a = loops[(i + 1) % 4].vert.co - loops[i].vert.co
            b = loops[(i + 3) % 4].vert.co - loops[(i + 2) % 4].vert.co
            if a.dot(b) < 0:
                b = -b
            candidates.append((a + b) * .5)
        world = obj.matrix_world.to_3x3()
        candidates.sort(key=lambda v: (world @ v).length, reverse=True)
        if (world @ candidates[0]).length < 1.15 * (world @ candidates[1]).length:
            raise ValueError('This face has no clear long direction. Switch to Edge Select and select one lengthwise edge on this face.')
        tangent = candidates[0]
        normal, origin = face.normal.copy(), face.calc_center_median()
    elif not faces and len(edges) == 1:
        edge = edges[0]
        adjacent = [f for f in edge.link_faces if not f.hide]
        face = bm.faces.active if bm.faces.active in adjacent else None
        if face is None and len(adjacent) == 1:
            face = adjacent[0]
        if face is None:
            raise ValueError('This edge has two sides. Select the intended face first, then switch to Edge Select and select its lengthwise edge.')
        tangent = edge.verts[1].co - edge.verts[0].co
        faces = [face]
        normal, origin = face.normal.copy(), face.calc_center_median()
    else:
        raise ValueError('Select a continuous top face strip, one face, or one longitudinal edge.')
    frame(tangent, normal)
    vertices = sorted({v for f in faces for v in f.verts}, key=lambda v: v.index)
    record = {
        'schema': 2, 'normal_sign': -1,
        'faces': [{'index': f.index, 'vertices': [v.index for v in f.verts]} for f in faces],
        'vertices': [v.index for v in vertices],
        'coordinates': [list(v.co) for v in vertices],
        'counts': [len(bm.verts), len(bm.edges), len(bm.faces)],
        'origin': list(origin),
        'tangent': list(tangent.normalized()), 'normal': list(normal),
        'size': max((obj.matrix_world.to_3x3() @ tangent).length * .4, .005),
    }
    settings = state(context)
    settings.mesh = obj
    settings.guide = json.dumps(record)
    settings.flip_forward = False
    settings.flip_bend = False
    return guide_frame(context)


def guide_frame(context):
    settings = state(context)
    obj = settings.mesh
    if obj is None or obj.type != 'MESH' or not settings.guide:
        raise ValueError('Capture a face or edge to define the bend direction first.')
    record = json.loads(settings.guide)
    captured_faces = record.get('faces', [{'index': record.get('face', -1), 'vertices': record['vertices']}])
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        counts = [len(bm.verts), len(bm.edges), len(bm.faces)]
        vertices = bm.verts
        valid_faces = all(0 <= f['index'] < len(bm.faces) and
                          [v.index for v in bm.faces[f['index']].verts] == f['vertices'] for f in captured_faces)
    else:
        counts = [len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons)]
        vertices = obj.data.vertices
        valid_faces = all(0 <= f['index'] < len(obj.data.polygons) and
                          list(obj.data.polygons[f['index']].vertices) == f['vertices'] for f in captured_faces)
    if counts != record['counts'] or not valid_faces or any(
        (vertices[i].co - Vector(co)).length > 1e-6
        for i, co in zip(record['vertices'], record['coordinates'])
    ):
        raise ValueError('The captured topology or face changed; capture the guide again.')
    world = obj.matrix_world.to_3x3()
    if abs(world.determinant()) < EPS:
        raise ValueError('The guide mesh has zero scale.')
    tangent = world @ Vector(record['tangent'])
    normal = (world.inverted().transposed() @ Vector(record['normal'])) * record.get('normal_sign', 1)
    if settings.flip_forward:
        tangent.negate()
    if settings.flip_bend:
        normal.negate()
    t, b, a = frame(tangent, normal)
    return obj.matrix_world @ Vector(record['origin']), t, b, a, record['size']


def plan(context):
    from . import finger_bones as bones
    rig = context.object
    if rig is None or rig.type != 'ARMATURE' or rig.mode != 'EDIT':
        raise ValueError('Enter Armature Edit Mode and select one continuous finger chain.')
    if rig.data.users != 1 or rig.library or rig.data.library or rig.override_library:
        raise ValueError('Use a local, single-user armature for rest-axis calibration.')
    groups, ignored, _ = bones._selected_finger_groups(rig)
    if ignored or len(groups) != 1:
        raise ValueError('Select only one finger chain on one hand.')
    chain = sorted(next(iter(groups.values())), key=bones._parent_depth)
    if any(b.parent != a for a, b in zip(chain, chain[1:])):
        raise ValueError('Select a continuous parent-child finger chain.')
    world = rig.matrix_world.to_3x3()
    scales = [v.length for v in world.col]
    unit = [v.normalized() for v in world.col]
    if min(scales) < EPS or max(scales) - min(scales) > max(scales) * 1e-5 or world.determinant() <= 0 or any(
        abs(unit[i].dot(unit[j])) > 1e-5 for i, j in ((0, 1), (0, 2), (1, 2))
    ):
        raise ValueError('Apply non-uniform or mirrored armature scale before calibrating finger roll.')
    _, forward, bend, _, _ = guide_frame(context)
    records = []
    for bone in chain:
        direction = (bone.tail - bone.head).normalized()
        if (world @ direction).normalized().dot(forward) < .5:
            raise ValueError('The blue arrow must point toward this finger tip. Reverse the Tip Arrow or capture a better lengthwise edge.')
        t, b, axis = frame(direction, world.inverted() @ bend)
        records.append({'name': bone.name, 'axis': axis, 'bend': b,
                        'head': bone.head.copy(), 'tail': bone.tail.copy(),
                        'roll': float(bone.roll), 'direction': t})
    return rig, records


def apply(context):
    rig, records = plan(context)
    # Reorienting animated bind axes needs animation conversion, outside this tool.
    if rig.animation_data and (rig.animation_data.action or rig.animation_data.nla_tracks or rig.animation_data.drivers):
        raise ValueError('This rig has animation or drivers; calibrate its rest axes before animation.')
    affected = {record['name'] for record in records}
    for record in records:
        affected.update(b.name for b in rig.data.edit_bones[record['name']].children_recursive)
    for name in affected:
        pb = rig.pose.bones.get(name)
        if pb is not None and (pb.constraints or any(
            # Neutral poses reconstructed through matrices can retain a few
            # single-precision ULPs (real X scale residual: 1.2e-6).
            abs(pb.matrix_basis[i][j] - Matrix.Identity(4)[i][j]) > 2e-6
            for i in range(4) for j in range(4)
        )):
            raise ValueError(f"'{name}' needs neutral pose transforms and no constraints before changing rest axes.")
    try:
        for record in records:
            bone = rig.data.edit_bones[record['name']]
            bone.align_roll(record['axis'].cross(record['direction']))
            # Test actual positive rotation, not merely the intended axis formula.
            delta = Quaternion(bone.x_axis, math.radians(5)) @ record['direction'] - record['direction']
            if delta.normalized().dot(record['bend']) < .99:
                raise ValueError('Positive rotation did not follow the bend arrow; calibration was rolled back.')
        rig.update_tag(refresh={'DATA'})
        context.view_layer.update()
    except Exception:
        for record in records:
            rig.data.edit_bones[record['name']].roll = record['roll']
        rig.update_tag(refresh={'DATA'})
        context.view_layer.update()
        raise
    return len(records)


def arrow(lines, origin, direction, length, side):
    tip = origin + direction * length
    lines.extend((tuple(origin), tuple(tip)))
    for sign in (-1, 1):
        lines.extend((tuple(tip), tuple(tip - direction * length * .22 + side * length * .11 * sign)))


def arc(lines, origin, tangent, axis, length):
    previous = origin + tangent * length
    for i in range(1, 13):
        direction = Quaternion(axis, math.radians(45 * i / 12)) @ tangent
        point = origin + direction * length
        lines.extend((tuple(previous), tuple(point)))
        previous = point
    arrow(lines, previous - axis.cross(direction) * length * .15,
          axis.cross(direction), length * .15, direction)


def preview_lines(context):
    origin, t, b, a, size = guide_frame(context)
    forward, bend, curves, target_axes, current_axes = [], [], [], [], []
    arrow(forward, origin, t, size, b)
    arrow(bend, origin, b, size * .7, t)
    if context.object and context.object.type == 'ARMATURE' and context.object.mode == 'EDIT':
        rig, records = plan(context)
        for record in records:
            head = rig.matrix_world @ record['head']
            tail = rig.matrix_world @ record['tail']
            axis = (rig.matrix_world.to_3x3() @ record['axis']).normalized()
            length = (tail - head).length
            direction = (tail - head).normalized()
            bone = rig.data.edit_bones[record['name']]
            current = (rig.matrix_world.to_3x3() @ bone.x_axis).normalized()
            # Both hinges go through the existing bone head inside the finger.
            target_axes.extend((tuple(head - axis * length * .3), tuple(head + axis * length * .3)))
            current_axes.extend((tuple(head - current * length * .25), tuple(head + current * length * .25)))
            arrow(bend, head, axis.cross(direction), length * .45, direction)
            arrow(forward, head, direction, length, axis)
            arc(curves, head, direction, axis, length)
            # Wire octahedron previews the proposed Roll at unchanged Head/Tail.
            center = head.lerp(tail, .1)
            z = axis.cross(direction)
            corners = [center + v * length * .1 for v in (axis, z, -axis, -z)]
            for i, corner in enumerate(corners):
                target_axes.extend((tuple(head), tuple(corner), tuple(corner), tuple(tail),
                                    tuple(corner), tuple(corners[(i + 1) % 4])))
    else:
        # This short arc communicates direction only; real hinges appear at
        # the bone centers as soon as the artist selects the target chain.
        arc(curves, origin, t, a, size)
    return forward, bend, curves, target_axes, current_axes


def _draw():
    if not _visible:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        groups = preview_lines(bpy.context)
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        depth, width = gpu.state.depth_test_get(), gpu.state.line_width_get()
        try:
            gpu.state.depth_test_set('NONE')
            gpu.state.line_width_set(3)
            shader.bind()
            for points, color in zip(groups, ((.1, .65, 1, 1), (1, .4, .05, 1), (.2, 1, .3, 1), (.8, .35, 1, 1), (1, .12, .12, 1))):
                if not points:
                    continue
                shader.uniform_float('color', color)
                batch_for_shader(shader, 'LINES', {'pos': points}).draw(shader)
        finally:
            gpu.state.depth_test_set(depth)
            gpu.state.line_width_set(width)
    except (ValueError, ReferenceError, KeyError, RuntimeError):
        pass


def show_preview():
    global _visible, _handle
    _visible = True
    if _handle is None and not bpy.app.background:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')


class CHARACTERDESIGNER_OT_finger_flex(Operator):
    bl_idname = 'character_designer.finger_flex'
    bl_label = 'Finger Bend Direction'
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(items=[(key, label, label) for key, label in (
        ('CAPTURE', 'Capture Top Strip / Edge'), ('PREVIEW', 'Preview Roll + Bend'),
        ('HIDE', 'Hide Preview'), ('APPLY', 'Calibrate Bone Roll'))])

    def execute(self, context):
        global _visible
        settings = state(context)
        try:
            if self.action == 'CAPTURE':
                capture(context)
                show_preview()
                settings.status = 'Top surface captured. Orange bends inward. Next: select the finger bones in Armature Edit Mode to preview and calibrate Roll.'
            elif self.action == 'PREVIEW':
                preview_lines(context)
                show_preview()
                settings.status = 'Purple = proposed Roll and center hinge; red = current axis; green = positive bend arc.'
            elif self.action == 'HIDE':
                _visible = False
                settings.status = 'Bend preview hidden.'
            else:
                count = apply(context)
                settings.status = f'Aligned {count} finger bones. Positive Local X bends toward orange.'
            from .finger_bones import _tag_redraw
            _tag_redraw()
            self.report({'INFO'}, settings.status)
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
            settings.status = str(exc)
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw_controls(layout, context):
    settings = state(context)
    box = layout.box()
    box.label(text='Finger Top Surface / Bone Roll', icon='ORIENTATION_NORMAL')
    box.label(text='1. Mesh Edit Mode: select the top strip.')
    row = box.row()
    row.enabled = context.mode == 'EDIT_MESH'
    row.operator('character_designer.finger_flex', text='Capture Top Strip / Edge').action = 'CAPTURE'
    if settings.mesh and settings.guide:
        box.label(text=settings.mesh.name, icon='MESH_DATA')
        box.prop(settings, 'flip_forward')
        box.prop(settings, 'flip_bend')
        row = box.row(align=True)
        row.operator('character_designer.finger_flex', text='Preview Roll + Bend').action = 'PREVIEW'
        row.operator('character_designer.finger_flex', text='Hide').action = 'HIDE'
        box.label(text='Blue: tip | Orange: inward bend')
        box.label(text='Purple: target Roll | Red: current axis')
    else:
        box.label(text='Select the top row of faces along one finger,')
        box.label(text='or a lengthwise edge on its top face.')
    box.label(text='2. Select finger bones in Armature Edit Mode.')
    row = box.row()
    row.enabled = context.mode == 'EDIT_ARMATURE' and bool(settings.guide and settings.mesh)
    row.operator('character_designer.finger_flex', text='3. Calibrate Bone Roll').action = 'APPLY'
    box.label(text='Pose Mode: R X X, positive angle bends inward.')
    if settings.status:
        # Wrap long diagnostic messages instead of widening the sidebar.
        import textwrap
        for line in textwrap.wrap(settings.status, width=48):
            box.label(text=line)


CLASSES = (CharacterDesignerFingerFlexState, CHARACTERDESIGNER_OT_finger_flex)


def register_runtime():
    if not hasattr(bpy.types.Scene, 'character_designer_finger_flex'):
        bpy.types.Scene.character_designer_finger_flex = PointerProperty(type=CharacterDesignerFingerFlexState)


def unregister_runtime():
    global _handle, _visible
    _visible = False
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    if hasattr(bpy.types.Scene, 'character_designer_finger_flex'):
        del bpy.types.Scene.character_designer_finger_flex
