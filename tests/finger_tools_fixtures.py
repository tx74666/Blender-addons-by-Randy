"""Shared data-only fixtures for retained Finger Basic Setup and bone tools."""
import json
import math
import sys
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
import character_designer
from character_designer import finger_bank as bank, finger_definition as definition, finger_targets as targets
from character_designer.mesh_mirror import _fingerprint

C = bpy.context


def fingerprint(obj):
    """Read every artist-data layer without depending on retired ring tools."""
    if obj.mode != 'EDIT': return _fingerprint(obj)
    obj.update_from_editmode()
    probe, mesh = obj.copy(), obj.data.copy()
    try:
        probe.data = mesh
        return _fingerprint(probe)
    finally:
        bpy.data.objects.remove(probe)
        bpy.data.meshes.remove(mesh)


def hands_fixture():
    if C.object and C.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
    for obj in list(bpy.data.objects): bpy.data.objects.remove(obj, do_unlink=True)
    vertices, faces, chosen = [], [], {}
    specs = [('THUMB', (-.6, -.4, 0), (-.55, .83, 0), .75),
             ('INDEX', (-.3, 0, 0), (-.07, 1, 0), 1.05),
             ('MIDDLE', (-.1, .025, 0), (0, 1, 0), 1.15),
             ('RING', (.1, .005, 0), (.03, 1, 0), 1.07),
             ('PINKY', (.3, -.045, 0), (.15, 1, 0), .82)]
    for side, sign in (('L', 1), ('R', -1)):
        for digit, root, tangent, length in specs:
            root, t = Vector(root)+Vector((3, 0, 0)), Vector(tangent).normalized()
            across = Vector((t.y, -t.x, 0))
            first, face_first = len(vertices), len(faces)
            for i in range(7):
                for j in range(8):
                    p = root+t*(length*i/6)+across*(.065*math.cos(j*math.tau/8))+Vector((0, 0, .065*math.sin(j*math.tau/8)))
                    vertices.append((sign*p.x, p.y, p.z))
            for i in range(6):
                for j in range(8):
                    a, b = first+i*8+j, first+i*8+(j+1)%8
                    f = (a, a+8, b+8, b)
                    faces.append(f if sign > 0 else tuple(reversed(f)))
            f = tuple(reversed(range(first+48, first+56)))
            faces.append(f if sign > 0 else tuple(reversed(f)))
            for j in range(8):
                p = root-t*.5+across*(.5*math.cos(j*math.tau/8))+Vector((0, 0, .5*math.sin(j*math.tau/8)))
                vertices.append((sign*p.x, p.y, p.z))
            for j in range(8):
                a, b = first+j, first+(j+1)%8
                f = (a, b, first+56+(j+1)%8, first+56+j)
                faces.append(f if sign > 0 else tuple(reversed(f)))
            chosen[f'{digit}.{side}'] = [face_first+i*8+2 for i in range(6)]
    mesh = bpy.data.meshes.new('Hands')
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new('UnboundHands', mesh)
    C.collection.objects.link(obj)
    C.view_layer.objects.active = obj
    obj.select_set(True)
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Artist')
    for p in key.data: p.co.z += .01
    obj.vertex_groups.new(name='ArtistWeight').add(list(range(len(vertices))), .4, 'REPLACE')
    bpy.ops.object.mode_set(mode='EDIT')
    C.tool_settings.mesh_select_mode = (False, False, True)
    return obj, chosen

def select(obj, ids):
    bm = bmesh.from_edit_mesh(obj.data)
    for seq in (bm.verts, bm.edges, bm.faces):
        seq.ensure_lookup_table()
        for item in seq: item.select_set(False)
    for i in ids: bm.faces[i].select_set(True)
    bmesh.update_edit_mesh(obj.data)

def refused(fn):
    try: fn()
    except ValueError: return
    raise AssertionError('Ambiguous capture accepted')

def capture_all(obj, chosen):
    for digit in ('PINKY', 'INDEX', 'THUMB', 'RING', 'MIDDLE'):
        for side in ('L', 'R'):
            select(obj, chosen[f'{digit}.{side}'])
            assert bank.capture(C) == f'{digit}.{side}'
            definition.confirm(C)
            bank.sync(C)
    # Keep the fixture's previous active side and face selection; the runtime
    # no longer creates an opposite definition as a side effect of capture.
    select(obj, chosen['MIDDLE.L'])
    bank.select(C, 'MIDDLE', 'L')

def bound_fixture():
    obj, chosen = hands_fixture()
    capture_all(obj, chosen)
    bodies = json.loads(obj.character_designer_finger_bank.survey)['candidates']
    bpy.ops.object.mode_set(mode='OBJECT')
    rig = bpy.data.objects.new('MainRig', bpy.data.armatures.new('MainRig'))
    C.collection.objects.link(rig)
    obj.select_set(False); rig.select_set(True); C.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    for key, body in bodies.items():
        digit, side = key.split('.')
        root, tip = Vector(body['root']), Vector(body['tip'])
        previous = None
        count = 2 if digit == 'THUMB' else 3
        for i in range(count):
            b = rig.data.edit_bones.new(f'f_{digit.lower()}.{i+1:02d}.{side}')
            b.head, b.tail = root.lerp(tip, i/count), root.lerp(tip, (i+1)/count)
            b.parent, b.use_connect, b.roll = previous, False, .7
            previous = b
    extra = rig.data.edit_bones.new('OtherDeform')
    extra.head, extra.tail = (0, 0, 0), (0, 1, 0)
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.select_set(False); obj.select_set(True); C.view_layer.objects.active = obj
    for bone in rig.data.bones: obj.vertex_groups.new(name=bone.name)
    obj.vertex_groups['OtherDeform'].add(list(range(len(obj.data.vertices))), .1, 'REPLACE')
    obj.vertex_groups['OtherDeform'].lock_weight = True
    for key, body in bodies.items():
        digit, side = key.split('.')
        obj.vertex_groups[f'f_{digit.lower()}.01.{side}'].add(body['vertices'], .9, 'REPLACE')
    obj.modifiers.new('Rig', 'ARMATURE').object = rig
    C.scene.character_designer_setup.rig, C.scene.character_designer_setup.body = rig, obj
    bpy.ops.object.mode_set(mode='EDIT')
    bank.select(C, 'INDEX', 'L')
    return obj, rig, chosen

def single_finger_fixture(rooted=False):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    # Closed end caps are deliberately outside the selected top strip.
    vertices = [(math.cos(j*math.tau/8)*(.3+.03*math.sin(i)),
                 math.sin(j*math.tau/8)*.22, i*.5)
                for i in range(7) for j in range(8)]
    faces = [(i*8+j, i*8+(j+1)%8, (i+1)*8+(j+1)%8, (i+1)*8+j)
             for i in range(6) for j in range(8)]
    faces += [tuple(reversed(range(8))), tuple(range(48, 56))]
    root_faces = []
    if rooted:
        faces.pop(48)
        # Deliberately irregular triangulated palm transition, not a 3-to-1 fan.
        for z, radius in ((-.4, .35), (-3., 2.)):
            vertices += [(math.cos(j*math.tau/8)*radius, math.sin(j*math.tau/8)*radius, z) for j in range(8)]
        for j in range(8):
            a, b, c, d = j, (j+1)%8, 56+(j+1)%8, 56+j
            if j == 0: root_faces.extend((len(faces), len(faces)+1))
            faces.extend(((a, d, c), (a, c, b)))
            faces.append((56+j, 64+j, 64+(j+1)%8, 56+(j+1)%8))
        faces.append(tuple(reversed(range(64, 72))))
    extra = len(vertices)
    vertices += [(5, 0, 0), (6, 0, 0), (5, 1, 0)]
    faces += [(extra, extra+1, extra+2)]
    mesh = bpy.data.meshes.new('FingerLayoutFixture')
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new('FingerLayoutFixture', mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Artist')
    for i, point in enumerate(key.data):
        point.co.x += .01*i
    key.value = .35
    uv = mesh.uv_layers.new(name='ArtistUV')
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            vi = mesh.loops[li].vertex_index
            uv.data[li].uv = (vi % 8 / 8, vertices[vi][2]/3)
        poly.use_smooth = True
    group = obj.vertex_groups.new(name='finger.L')
    for v in mesh.vertices:
        group.add([v.index], max(0., min(1., v.co.z/3)), 'REPLACE')
    mesh.attributes.new('ArtistFloat', 'FLOAT', 'POINT')
    for d, v in zip(mesh.attributes['ArtistFloat'].data, mesh.vertices):
        d.value = v.co.z
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    for seq in (bm.faces, bm.edges, bm.verts):
        for item in seq: item.select_set(False)
    for i in range(6): bm.faces[i*8].select_set(True)
    if rooted:
        for i in root_faces: bm.faces[i].select_set(True)
    bmesh.update_edit_mesh(mesh)
    return obj

def rig_state(rig):
    """Include every bone, not just the two junctions expected to move."""
    return {
        'mirror': rig.data.use_mirror_x,
        'bones': {
            bone.name: {
                'head': tuple(bone.head_local), 'tail': tuple(bone.tail_local),
                'matrix': tuple(v for row in bone.matrix_local for v in row),
                'parent': bone.parent.name if bone.parent else '',
                'connected': bone.use_connect, 'deform': bone.use_deform,
            }
            for bone in rig.data.bones
        },
    }

def close(a, b, tolerance=2e-6):
    assert len(a) == len(b)
    assert max((abs(x-y) for x, y in zip(a, b)), default=0) <= tolerance, (a, b)

def assert_rig_equal(before, after, *, names=None):
    assert before['mirror'] == after['mirror']
    assert before['bones'].keys() == after['bones'].keys()
    for name in before['bones'] if names is None else names:
        a, b = before['bones'][name], after['bones'][name]
        for field in ('head', 'tail', 'matrix'): close(a[field], b[field])
        for field in ('parent', 'connected', 'deform'): assert a[field] == b[field], (name, field)

def assert_artist_data(obj):
    obj.update_from_editmode()
    keys = obj.data.shape_keys.key_blocks
    assert list(keys.keys()) == ['Basis', 'Artist']
    assert keys['Artist'].relative_key == keys['Basis']
    for basis, artist in zip(keys['Basis'].data, keys['Artist'].data):
        close(artist.co-basis.co, (0, 0, .01))
    assert obj.vertex_groups['OtherDeform'].lock_weight
    for vertex in obj.data.vertices:
        weights = {obj.vertex_groups[g.group].name: g.weight for g in vertex.groups}
        assert abs(weights['OtherDeform']-.1) < 1e-6
        assert abs(weights['ArtistWeight']-.4) < 1e-6

