"""Isolated native topology lookup prototype; no artist file is opened."""
import bpy
import bmesh
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
from character_designer import native_symmetry_pairs as native


def clone_table(coordinates, edges, faces):
    scene = bpy.data.scenes.new('Native lookup disposable scene')
    mesh = bpy.data.meshes.new('Native lookup disposable mesh')
    obj = bpy.data.objects.new('Native lookup disposable object', mesh)
    try:
        mesh.from_pydata(coordinates, edges, faces)
        scene.collection.objects.link(obj)
        layer = scene.view_layers[0]
        layer.objects.active = obj
        obj.select_set(True, view_layer=layer)
        scene.tool_settings.mesh_select_mode = (True, False, False)
        mesh.use_mirror_topology = True
        with bpy.context.temp_override(scene=scene, view_layer=layer, object=obj,
                                       active_object=obj, selected_objects=[obj],
                                       selected_editable_objects=[obj]):
            print('BEFORE_ENTER', bpy.context.mode, bpy.context.object.name)
            bpy.ops.object.mode_set(mode='EDIT')
            print('AFTER_ENTER', bpy.context.mode, bpy.context.edit_object.name)
            bm = bmesh.from_edit_mesh(mesh)
            bm.verts.ensure_lookup_table()
            result = [0] * len(bm.verts)
            for bit in range(len(bm.verts).bit_length()):
                for face in bm.faces:
                    face.select_set(False)
                for edge in bm.edges:
                    edge.select_set(False)
                for index, vert in enumerate(bm.verts):
                    vert.select_set(bool((index + 1) & (1 << bit)))
                bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
                response = bpy.ops.mesh.select_mirror(axis={'X'}, extend=False)
                print('BIT', bit, response, [v.index for v in bm.verts if v.select])
                for index, vert in enumerate(bm.verts):
                    if vert.select:
                        result[index] |= 1 << bit
            bpy.ops.object.mode_set(mode='OBJECT')
        return tuple(index - 1 for index in result)
    finally:
        if obj.mode == 'EDIT':
            with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0],
                                           object=obj, active_object=obj):
                bpy.ops.object.mode_set(mode='OBJECT')
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        bpy.data.scenes.remove(scene)


vertices = [(float(column - 2), row + row * row * .1,
             row * row * .02 + abs(column - 2) * .1)
            for row in range(4) for column in range(5)]
faces = [(row * 5 + column, row * 5 + column + 1,
          (row + 1) * 5 + column + 1, (row + 1) * 5 + column)
         for row in range(3) for column in range(4)]
vertices.extend(((-1.7, -.7, .3), (1.7, -.7, .3),
                 (-2.3, 1.3, .1), (2.3, 1.3, .1),
                 (-2.6, 1.5, .15), (2.6, 1.5, .15)))
faces.extend(((0, 1, 20), (3, 4, 21)))
edges = [(5, 22), (22, 24), (9, 23), (23, 25)]
artist = bpy.context.object
artist.data.use_mirror_topology = True
bpy.ops.object.mode_set(mode='EDIT')
artist_bm = bmesh.from_edit_mesh(artist.data)
artist_before = (bpy.context.scene, bpy.context.view_layer, bpy.context.mode,
                 tuple(v.select for v in artist_bm.verts),
                 tuple(tuple(v.co) for v in artist_bm.verts))
data_before = (len(bpy.data.scenes), len(bpy.data.objects), len(bpy.data.meshes))
print('LOOKUP', clone_table(vertices, edges, faces))
print('ARTIST_RETAINED', artist.mode, bpy.context.mode,
      artist_before == (bpy.context.scene, bpy.context.view_layer, bpy.context.mode,
                        tuple(v.select for v in artist_bm.verts),
                        tuple(tuple(v.co) for v in artist_bm.verts)))
print('DATA_RETAINED', data_before == (len(bpy.data.scenes), len(bpy.data.objects), len(bpy.data.meshes)))
print('MODULE_LOOKUP', native.native_vertex_lookup(vertices, edges, faces))
print('MODULE_PAIRS', native.build_vertex_pairs(vertices, edges, faces))
print('MODULE_READ_ONLY', artist_before == (bpy.context.scene, bpy.context.view_layer, bpy.context.mode,
                        tuple(v.select for v in artist_bm.verts),
                        tuple(tuple(v.co) for v in artist_bm.verts)),
      data_before == (len(bpy.data.scenes), len(bpy.data.objects), len(bpy.data.meshes)))
for axis in ('Y', 'Z'):
    permuted = [(point[1], point[0], point[2]) if axis == 'Y'
                else (point[2], point[1], point[0]) for point in vertices]
    print('AXIS_PAIRS', axis, native.build_vertex_pairs(permuted, edges, faces, axis=axis))
