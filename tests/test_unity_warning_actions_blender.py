"""Real warning actions: fresh vertex selection and reversible export-only materials."""
import json
import sys
import tempfile
from pathlib import Path
import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
import character_designer
from character_designer import unity_export_ui as ui

character_designer.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
armature = bpy.data.armatures.new('Bones')
rig = bpy.data.objects.new('Rig', armature)
bpy.context.scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
bone = armature.edit_bones.new('Head')
bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
bpy.ops.object.mode_set(mode='OBJECT')
mesh = bpy.data.meshes.new('Body Mesh')
mesh.from_pydata([(0,0,0), (1,0,0), (1,1,0), (0,1,0)], [], [(0,1,2,3)])
body = bpy.data.objects.new('Body', mesh)
bpy.context.scene.collection.objects.link(body)
body.modifiers.new('Armature', 'ARMATURE').object = rig
group = body.vertex_groups.new(name='Head')
group.add([0, 2], 1, 'REPLACE')
body.shape_key_add(name='Basis')
key = body.shape_key_add(name='Smile')
key.data[2].co.z += .2
key.value = .35
material = bpy.data.materials.new('Custom Material')
material.use_nodes = True
material.node_tree.nodes.new('ShaderNodeBsdfToon')
body.data.materials.append(material)
bpy.context.scene.character_designer_setup.rig = rig
bpy.context.scene.character_designer_setup.body = body
config = rig.character_designer_unity_export

def invariant():
    return (tuple(tuple(vertex.co) for vertex in mesh.vertices),
            tuple(tuple((weight.group, weight.weight) for weight in vertex.groups) for vertex in mesh.vertices),
            tuple((key.name, key.value, tuple(tuple(vertex.co) for vertex in key.data)) for key in mesh.shape_keys.key_blocks),
            tuple((bone.name, tuple(bone.matrix_local)) for bone in armature.bones),
            tuple((node.name, node.type) for node in material.node_tree.nodes),
            tuple((link.from_node.name, link.to_node.name) for link in material.node_tree.links))

before = invariant()
mesh.vertices[1].hide = True
assert bpy.ops.character_designer.unity_locate_unweighted(object_name='Body') == {'FINISHED'}
assert bpy.context.mode == 'EDIT_MESH'
bm = bmesh.from_edit_mesh(mesh)
# View framing may use BMVert.index as temporary scratch storage.
bm.verts.index_update()
assert [vertex.index for vertex in bm.verts if vertex.select] == [1, 3], [(vertex.index, vertex.select, vertex.hide) for vertex in bm.verts]
assert not any(vertex.hide for vertex in bm.verts if vertex.select)
bpy.ops.object.mode_set(mode='OBJECT')
assert invariant() == before

# The same old warning must use today's weights, never yesterday's two indices.
group.add([3], 1, 'REPLACE')
before = invariant()
assert bpy.ops.character_designer.unity_locate_unweighted(object_name='Body') == {'FINISHED'}
bmesh.from_edit_mesh(mesh).verts.index_update()
assert [vertex.index for vertex in bmesh.from_edit_mesh(mesh).verts if vertex.select] == [1]
bpy.ops.object.mode_set(mode='OBJECT')
assert invariant() == before
group.add([1], 1, 'REPLACE')
assert bpy.ops.character_designer.unity_locate_unweighted(object_name='Body') == {'FINISHED'}
assert bpy.context.mode == 'OBJECT'
print('PASS fresh source indices, hidden-point reveal, exact selection, fixed warnings and geometry/weights/keys preserved')

before = invariant()
assert bpy.ops.character_designer.unity_simple_material(material_name=material.name, enabled=True) == {'FINISHED'}
assert bpy.ops.character_designer.unity_simple_material(material_name=material.name, enabled=True) == {'FINISHED'}
assert len(config.simple_materials) == 1 and config.simple_materials[0].material == material
assert invariant() == before and body.data.materials[0] == material

with tempfile.TemporaryDirectory(prefix='cd-warning-actions-') as folder:
    path = str(Path(folder) / 'settings.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    rig = bpy.data.objects['Rig']; config = rig.character_designer_unity_export
    assert len(config.simple_materials) == 1
    assert config.simple_materials[0].material.name == 'Custom Material'
    assert bpy.ops.character_designer.unity_simple_material(material_name='Custom Material', enabled=False) == {'FINISHED'}
    assert not config.simple_materials
    assert any(node.type == 'BSDF_TOON' for node in bpy.data.materials['Custom Material'].node_tree.nodes)
print('PASS idempotent per-material choice, original shader preserved, save/reopen and restore')
bpy.context.preferences.edit.use_global_undo = True
bpy.ops.ed.undo_push(message='Before simple export choice')
assert bpy.ops.character_designer.unity_simple_material(material_name='Custom Material', enabled=True) == {'FINISHED'}
bpy.ops.ed.undo_push(message='After simple export choice')
assert bpy.ops.ed.undo() == {'FINISHED'}
assert not bpy.data.objects['Rig'].character_designer_unity_export.simple_materials
assert bpy.ops.ed.redo() == {'FINISHED'}
assert len(bpy.data.objects['Rig'].character_designer_unity_export.simple_materials) == 1
assert any(node.type == 'BSDF_TOON' for node in bpy.data.materials['Custom Material'].node_tree.nodes)
print('PASS actual Undo/Redo restores material export choice without changing shader')
character_designer.unregister()
print('UNITY_WARNING_ACTIONS_PASSED')
