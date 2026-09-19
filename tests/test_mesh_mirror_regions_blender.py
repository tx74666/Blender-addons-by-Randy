"""Whole-strand replacement and loop-delimited replacement regressions."""
import math
import sys
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mesh_mirror_blender import make, select, apply, mirror


def strand(levels=tuple(range(11)), reflected=False, offset=0.0, y_offset=0.0):
    vertices = []
    for z in levels:
        center = Vector((3 + .03*z*z, 1.8*math.sin(z*.25)+y_offset, z))
        radius = .42 - z*.018
        for j in range(8):
            angle = j*math.tau/8
            co = center + Vector((radius*math.cos(angle), radius*math.sin(angle), 0))
            if reflected:
                co.x = -co.x + offset*(1-z/10)
            vertices.append(tuple(co))
    faces = [tuple(reversed(range(8)))]
    faces += [(i*8+j, i*8+(j+1)%8, (i+1)*8+(j+1)%8, (i+1)*8+j)
              for i in range(len(levels)-1) for j in range(8)]
    faces += [tuple((len(levels)-1)*8+j for j in range(8))]
    if reflected:
        faces = [tuple(reversed(f)) for f in faces]
    return vertices, faces


def combine(parts):
    vertices, faces, spans = [], [], []
    for points, polygons in parts:
        start_v, start_f = len(vertices), len(faces)
        vertices.extend(points)
        faces.extend(tuple(i+start_v for i in p) for p in polygons)
        spans.append((tuple(range(start_v, len(vertices))), tuple(range(start_f, len(faces)))))
    return vertices, faces, spans


def print_plan(plan):
    print('MATCH_EVIDENCE', [(len(c.faces), round(c.score, 3), tuple(round(x, 3) for x in c.coverage), c.automatic) for c in plan.candidates], flush=True)


def test_damaged_whole_strand_auto_replaced_not_patched():
    for bound in (False, True):
        points, polygons, regions = combine((strand(), strand(tuple(i*.5 for i in range(21)), True, .20),
                                            strand(reflected=True, y_offset=2.0)))
        target_tip = regions[1][0][-1]
        points.append(tuple(Vector(points[target_tip]) + Vector((.1, .1, .1))))
        obj = make(points, polygons, [(target_tip, len(points)-1)])
        obj.shape_key_add(name='Basis')
        key = obj.shape_key_add(name='Artist')
        key.data[regions[0][0][-1]].co.y += .1
        modifier = None
        if bound:
            armature = bpy.data.armatures.new('ExistingRig')
            rig = bpy.data.objects.new('ExistingRig', armature)
            bpy.context.collection.objects.link(rig)
            obj.select_set(False)
            rig.select_set(True)
            bpy.context.view_layer.objects.active = rig
            bpy.ops.object.mode_set(mode='EDIT')
            for name, x in (('hair.L', 3), ('hair.R', -3)):
                bone = armature.edit_bones.new(name)
                bone.head, bone.tail = (x, 0, 0), (x, 0, 10)
            bpy.ops.object.mode_set(mode='OBJECT')
            rig.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            modifier = obj.modifiers.new('Existing Armature', 'ARMATURE')
            modifier.object = rig
            for name, vertices in (('hair.L', regions[0][0]), ('hair.R', regions[1][0])):
                group = obj.vertex_groups.new(name=name)
                group.add(vertices, .8 if name.endswith('L') else .3, 'REPLACE')
            bones_before = [(b.name, tuple(b.head_local), tuple(b.tail_local)) for b in armature.bones]
        # Delete a hole, but keep its edges and dangling vertices as damaged
        # geometry belonging to the same connected strand.
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[i] for i in regions[1][1][49:57]], context='FACES_ONLY')
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        neighbor = [tuple(obj.data.vertices[i].co) for i in regions[2][0]]
        before_source = [tuple(obj.data.vertices[i].co) for i in regions[0][0]]
        select(obj, regions[0][1])
        plan = mirror.build_plan(bpy.context)
        print_plan(plan)
        assert plan.region_kind == 'ISLAND' and not plan.needs_choice
        assert len(plan.target_vertices) == len(regions[1][0])+1
        assert len(plan.target_faces) == len(regions[1][1])-8
        bpy.ops.object.mode_set(mode='OBJECT')
        selection = mirror.apply_plan(plan)
        assert len(obj.data.vertices) == len(regions[0][0])*2 + len(regions[2][0])
        assert [tuple(obj.data.vertices[i].co) for i in selection] == before_source
        assert all(point in [tuple(v.co) for v in obj.data.vertices] for point in neighbor)
        assert not any(e.is_loose for e in obj.data.edges), 'Old target remnant must be removed in full'
        if bound:
            assert modifier.object == rig
            assert [(b.name, tuple(b.head_local), tuple(b.tail_local)) for b in armature.bones] == bones_before
            assert len(obj.vertex_groups) == 2
            right = next(s for s in mirror.weights._capture_vertex_groups(obj) if s.name == 'hair.R')
            assert len(right.weights) == len(regions[0][0]) and all(abs(w-.8)<1e-6 for _,w in right.weights)
        else:
            assert not obj.vertex_groups


def test_truncated_tip_can_match_remaining_distributed_surface():
    points, faces, regions = combine((strand(), strand(tuple(i*.5 for i in range(16)), True, .12)))
    obj = make(points, faces)
    select(obj, regions[0][1])
    plan = mirror.build_plan(bpy.context)
    print_plan(plan)
    assert not plan.needs_choice and set(plan.target_vertices) == set(regions[1][0])
    bpy.ops.object.mode_set(mode='OBJECT')
    mirror.apply_plan(plan)
    assert len(obj.data.vertices) == len(regions[0][0])*2


def test_loop_bounds_delete_everything_below_but_preserve_root():
    levels = (0, 1, 2, 3, 4) + tuple(4+i*.5 for i in range(1, 13))
    points, faces, regions = combine((strand(), strand(levels, True)))
    # A hole and dangling tip must be removed with the loop-bounded target,
    # without following the connected strand back across its root boundary.
    del faces[regions[1][1][-10]]
    tip = regions[1][0][-1]
    points.append(tuple(Vector(points[tip]) + Vector((.05, .05, .05))))
    obj = make(points, faces, [(tip, len(points)-1)])
    source_faces = [i for i in regions[0][1] if all(points[v][2] >= 4 for v in faces[i])]
    target_range = range(regions[1][1][0], len(faces))
    target_faces = [i for i in target_range if all(points[v][2] >= 4 for v in faces[i])]
    root_faces = [tuple(tuple(points[v]) for v in faces[i]) for i in target_range if i not in target_faces]
    select(obj, source_faces)
    plan = mirror.build_plan(bpy.context)
    print_plan(plan)
    assert plan.region_kind == 'LOOP_REGION' and not plan.needs_choice
    assert set(plan.target_faces) == set(target_faces) and len(plan.seams) == 8
    bpy.ops.object.mode_set(mode='OBJECT')
    mirror.apply_plan(plan)
    output_faces = [tuple(tuple(obj.data.vertices[v].co) for v in f.vertices) for f in obj.data.polygons]
    assert all(face in output_faces for face in root_faces)
    assert len(obj.data.polygons) == len(faces) - len(target_faces) + len(source_faces)
    assert not any(e.is_loose for e in obj.data.edges)


def test_overlapping_bounds_are_not_a_corresponding_strand():
    source = strand()
    # A large closed shell encloses the mirror bounds, but its surface is not
    # the counterpart. Bounding-box overlap alone must not block a new strand.
    box = ([(-20,-20,-20), (20,-20,-20), (20,20,-20), (-20,20,-20),
            (-20,-20,20), (20,-20,20), (20,20,20), (-20,20,20)],
           [(0,3,2,1),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7),(4,5,6,7)])
    points, faces, regions = combine((source, box))
    obj = make(points, faces)
    select(obj, regions[0][1])
    plan, _ = apply(obj)
    assert not plan.target_vertices and not plan.candidates
    assert len(obj.data.vertices) == len(source[0])*2+8


def test_cannot_combine_two_close_strands_as_one_target():
    points, faces, regions = combine((strand(), strand(reflected=True, offset=.06),
                                     strand(reflected=True, offset=-.06)))
    obj = make(points, faces)
    select(obj, regions[0][1])
    plan = mirror.build_plan(bpy.context)
    print_plan(plan)
    assert plan.needs_choice and len(plan.candidates) == 2 and not plan.target_faces
    chosen, _ = apply(obj, target_candidate=1)
    assert set(chosen.target_vertices) == set(regions[1][0])
    assert len(obj.data.vertices) == len(points)


def test_edit_ui_is_one_universal_action_without_hint_labels():
    from test_weight_symmetry_blender import OperatorCaptureLayout
    from character_designer import weight_symmetry
    obj = make(*strand())
    select(obj, range(len(obj.data.polygons)))
    class Layout(OperatorCaptureLayout):
        def label(self, **kwargs):
            raise AssertionError('No binding / coordinate / Shift hint labels in the panel')
    layout = Layout()
    weight_symmetry.draw_weight_symmetry(layout, bpy.context)
    assert [call[0] for call in layout.calls] == ['character_designer.mirror_selected_region', 'character_designer.mesh_mirror_preview']


if __name__ == '__main__':
    try:
        tests = [test for name,test in globals().copy().items() if name.startswith('test_')]
        for test in tests:
            test()
            print('PASS', test.__name__, flush=True)
        print('MESH_MIRROR_REGION_TESTS_PASSED', len(tests), flush=True)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
