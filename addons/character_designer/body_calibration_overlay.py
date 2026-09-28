"""Read-only viewport lines. No datablocks, timers, or mesh traversal per frame."""
import bpy
import blf
import math
from mathutils import Vector
from bpy.app.handlers import persistent
from . import body_calibration as service, body_calibration_display as display, limb_ik

_handles = []
_KEY = 'character_designer_body_calibration_handlers'
_batch_source = _batch = _shader = None


def segments(context):
    rig = context.object
    if not rig or rig.type != 'ARMATURE' or not service.settings(rig).show_directions:
        return [], []
    s = service.settings(rig)
    part = service.selected_part(rig)
    if part not in service.PARTS:
        return [], []
    key = ('segments', part, s.show_details,
           tuple(v for row in rig.matrix_world for v in row))
    return display.get(context, rig, key, lambda: _segments(context))


def _segments(context):
    rig = context.object
    if not rig or rig.type != 'ARMATURE' or not service.settings(rig).show_directions:
        return [], []
    settings = service.settings(rig)
    part = service.selected_part(rig)
    if part not in service.PARTS:
        return [], []
    detail = settings.show_details
    kind = 'LEG' if part == 'LEGS' else 'ARM'
    lines, labels = [], []
    world = rig.matrix_world
    def line(a, b, color, dashed=False):
        a, b = world @ Vector(a), world @ Vector(b)
        if dashed:
            for i in range(0, 12, 2):
                lines.append((a.lerp(b, i/12), a.lerp(b, (i+1)/12), color))
        else:
            lines.append((a, b, color))
    def label(point, text):
        if text: labels.append((world @ Vector(point), text))
    def arrow(a, d, size, text, dashed=False, color=(1, .65, .1, 1)):
        if d.length < 1e-8:
            return
        d = d.normalized()
        b = a + d*size
        line(a, b, color, dashed)
        cross = d.orthogonal().normalized()*size*.15
        line(b, b-d*size*.22+cross, color, dashed)
        line(b, b-d*size*.22-cross, color, dashed)
        label(b, text)
    pose = rig.mode == 'POSE'
    evaluated = rig.evaluated_get(context.evaluated_depsgraph_get()) if pose else rig
    candidate = display.status(context,rig,part).get('plan',{})
    if part=='ARMS':
        colors=((1,.2,.2,1),(.2,1,.2,1),(.3,.5,1,1))
        frames = candidate.get('wrists', ())
        mapped = candidate.get('mappings', {})
        chains = [limb_ik.LimbChain('ARM', side, *mapped['ARM.'+side]) for side in limb_ik.SIDES
                  if 'ARM.'+side in mapped] if mapped else service.chains(context,rig,'ARM')
        for chain in chains:
            state=service.rest(rig,chain.end)
            head,tail=Vector(state['head']),Vector(state['tail'])
            size=(tail-head).length
            frame=next((f for f in frames if f['side']==chain.side),None)
            if frame is not None:
                pivot=Vector(frame['wrist'])
                axes=[Vector(frame[k]) for k in ('x','y','z')]
            else:
                # An invalid arm candidate has no wrist frame. Show current
                # Rest axes; the panel explains what needs attention.
                if rig.mode=='EDIT':mat=rig.data.edit_bones[chain.end].matrix
                else:mat=rig.data.bones[chain.end].matrix_local
                pivot=mat.translation
                axes=mat.to_3x3().col
            for i,color in enumerate(colors):
                arrow(pivot,axes[i],size*.7,'XYZ'[i],color=color)
            if frame is not None:
                # One continuous flexion arc; no extra plane or second triad.
                points=[pivot+(axes[1]*math.cos(t*.09)+axes[2]*math.sin(t*.09))*size*.5 for t in range(17)]
                for a,b in zip(points,points[1:]):line(a,b,colors[0])
                tip=points[-1];tangent=(tip-points[-2]).normalized()
                cross=axes[0].cross(tangent)*size*.055
                for sign in (-1,1):line(tip,tip-tangent*size*.12+cross*sign,colors[0])
                start=Vector(frame['forearm_start'])
                arrow(start.lerp(pivot,.7),Vector(frame['forearm_axis']),size,'Forearm',color=(.2,1,1,1))
                label(pivot,f"{frame['axis_difference_degrees']:.2f}°")
    inv = limb_ik._validate_inventory(rig) if rig.mode != 'EDIT' else service.edit_inventory(rig)
    for chain in service.chains(context, rig, kind):
        s, e, w, bend = service.direction(rig, chain)
        total = (e-s).length+(w-e).length
        size = total*.13
        # The one wrist triad above is the exact arm candidate; never draw a
        # second current/control wrist frame on top of it.
        names = list(chain.names[:2] if kind=='ARM' else chain.names)
        existing = inv['rigs'].get((kind, chain.side))
        if kind=='ARM' and existing and 'target' in existing:
            wrist = existing['target'].head if rig.mode=='EDIT' else evaluated.pose.bones[existing['target'].name].head
            label(wrist,'Wrist')
        if kind!='ARM' and existing and 'target' in existing:
            names.append(existing['target'].name)
        for name in names:
            if rig.mode == 'EDIT':
                b = rig.data.edit_bones.get(name)
                if not b: continue
                center, mat = (b.head+b.tail)*.5, b.matrix
            elif pose:
                b = evaluated.pose.bones[name]
                center, mat = (b.head+b.tail)*.5, b.matrix
            else:
                b = rig.data.bones[name]
                center, mat = (b.head_local+b.tail_local)*.5, b.matrix_local
            for i, color in enumerate(((1,.2,.2,1),(.2,1,.2,1),(.3,.5,1,1))):
                arrow(center, mat.to_3x3().col[i], size*.55, 'XYZ'[i], color=color)
            if detail: label(center,name + (' [Pose]' if pose else ' [Rest]'))
            elif existing and 'target' in existing and name == existing['target'].name: label(center,'Wrist' if kind=='ARM' else 'Foot IK')
        if pose:
            upper, lower = evaluated.pose.bones[chain.upper], evaluated.pose.bones[chain.lower]
            s, e, w = upper.head, lower.head, lower.tail
            raw = limb_ik._project_perpendicular(e-s, w-s)
            from .body_calibration_math import stable_bend
            stable = stable_bend(s,e,w,limb_ik.EPSILON)
            bend = raw.normalized() if stable else None
        if bend is not None:
            arrow(e, bend, size, ('Bend ' + ('Pose' if pose else 'Rest')) if detail else 'Bend')
        else:
            label(e, 'Bend: unstable')
        rs,re,rw,_ = service.direction(rig,chain)
        wanted = limb_ik._project_perpendicular(service.target(rig, kind), rw-rs)
        arrow(re, wanted, size*1.5, 'Target [Rest local]' if detail else 'Target', True)
        proposed = next((p for p in candidate.get('limbs',()) if p.get('side') == chain.side and 'joint' in p),None)
        if proposed:
            point = Vector(proposed['joint'])
            line(rs,point,(.2,1,1,1),True)
            line(point,rw,(.2,1,1,1),True)
            line(re,point,(1,1,.2,1),True)
            if detail: label(point,f"Candidate: shift {proposed['shift']:.5g}")
            marker = total*.008
            for delta in (Vector((marker,0,0)),Vector((0,marker,0)),Vector((0,0,marker))):
                line(point-delta,point+delta,(.2,1,1,1))
        if existing and 'pole' in existing:
            pole = existing['pole'].head if rig.mode == 'EDIT' else evaluated.pose.bones[existing['pole'].name].head
            line(e, pole, (.8,.5,1,1))
            label(pole, 'Pole [evaluated]' if pose else 'Pole [Edit Rest]' if rig.mode == 'EDIT' else 'Pole [current]')
        elif wanted.length > 1e-8:
            planned_joint = Vector(proposed['joint']) if proposed else re
            planned_direction = Vector(proposed['direction']) if proposed else wanted.normalized()
            pole = planned_joint+planned_direction*total*settings.pole_distance
            line(planned_joint, pole, (.8,.5,1,1), True)
            label(pole, 'Not generated: planned Pole' if detail else 'Pole (planned)')
    return lines, labels


def _draw(pixel=False):
    global _batch_source, _batch, _shader
    context = bpy.context
    if not context.area or context.area.type != 'VIEW_3D': return
    try:
        lines, labels = segments(context)
        if pixel:
            from bpy_extras.view3d_utils import location_3d_to_region_2d
            blf.size(0, 11)
            blf.color(0, 1, 1, 1, 1)
            placed = []
            for position, text in labels:
                p = location_3d_to_region_2d(context.region, context.region_data, position)
                if p is not None:
                    width,height = blf.dimensions(0,text)
                    x,y = max(2,min(p.x+4,context.region.width-width-4)),p.y+4
                    for _ in range(20):
                        if not any(x < a+w+4 and x+width+4 > a and y < b+h+3 and y+height+3 > b for a,b,w,h in placed): break
                        y += height+5
                    placed.append((x,y,width,height))
                    blf.position(0, x,y,0)
                    blf.draw(0, text)
        elif lines:
            import gpu
            from gpu_extras.batch import batch_for_shader
            if _shader is None: _shader = gpu.shader.from_builtin('SMOOTH_COLOR')
            if _batch_source is not lines:
                _batch = batch_for_shader(_shader, 'LINES', {'pos': [v for a,b,c in lines for v in (a,b)],
                                                            'color': [c for a,b,c in lines for _ in (0,1)]})
                _batch_source = lines
            _shader.bind()
            _batch.draw(_shader)
    except (ValueError, RuntimeError, ReferenceError, KeyError, TypeError):
        # Invalid/mid-undo input is reported by Setup; drawing must remain read-only.
        return


@persistent
def _load(_unused):
    register()


def register():
    global _batch_source, _batch, _shader
    _batch_source = _batch = _shader = None
    display.register()
    old = bpy.app.driver_namespace.pop(_KEY, []) or list(_handles)
    for h in old:
        try: bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except (ValueError, ReferenceError): pass
    _handles.clear()
    if not bpy.app.background:
        _handles.extend([bpy.types.SpaceView3D.draw_handler_add(_draw, (False,), 'WINDOW', 'POST_VIEW'),
                         bpy.types.SpaceView3D.draw_handler_add(_draw, (True,), 'WINDOW', 'POST_PIXEL')])
    bpy.app.driver_namespace[_KEY] = list(_handles)
    for fn in tuple(bpy.app.handlers.load_post):
        if fn.__module__ == __name__ and fn.__name__ == '_load': bpy.app.handlers.load_post.remove(fn)
    bpy.app.handlers.load_post.append(_load)


def unregister():
    global _batch_source, _batch, _shader
    _batch_source = _batch = _shader = None
    display.unregister()
    handles = bpy.app.driver_namespace.pop(_KEY, []) or list(_handles)
    for h in handles:
        try: bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except (ValueError, ReferenceError): pass
    _handles.clear()
    for fn in tuple(bpy.app.handlers.load_post):
        if fn.__module__ == __name__ and fn.__name__ == '_load': bpy.app.handlers.load_post.remove(fn)
