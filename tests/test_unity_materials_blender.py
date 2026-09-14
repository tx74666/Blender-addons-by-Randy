"""Export-only material approximation checks, including a real FBX round trip.

Run Blender --factory-startup -b --python tests/test_unity_materials_blender.py
"""
import importlib.util
from pathlib import Path
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('unity_materials', ROOT / 'addons/character_designer/unity_materials.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def mesh(name):
    data = bpy.data.meshes.new(name + ' Mesh')
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    data.uv_layers.new(name='UVMap')
    return obj


def material(name='Stocking Main'):
    value = bpy.data.materials.new(name)
    value.use_nodes = True
    shader = value.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value = (0.018776, 0.018776, 0.018776, 1)
    shader.inputs['Roughness'].default_value = 0.5
    shader.inputs['Metallic'].default_value = 0
    return value, shader


def graph(value):
    return ([(node.name, node.bl_idname) for node in value.node_tree.nodes],
            [(link.from_node.name, link.from_socket.name, link.to_node.name, link.to_socket.name)
             for link in value.node_tree.links])


def close(actual, expected, tolerance=1e-5):
    assert len(actual) == len(expected)
    assert max(abs(float(a) - float(b)) for a, b in zip(actual, expected)) < tolerance, (actual, expected)


def test_source_slots_and_idempotence():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = mesh('Stocking')
    source, shader = material()
    procedural = source.node_tree.nodes.new('ShaderNodeTexMagic')
    source.node_tree.links.new(procedural.outputs['Color'], shader.inputs['Base Color'])
    obj.data.materials.append(source)
    other, _ = material('Unchanged')
    obj.data.materials.append(other)
    sibling = obj.copy()
    bpy.context.scene.collection.objects.link(sibling)
    before, coordinates = graph(source), [tuple(vertex.co) for vertex in obj.data.vertices]
    original_data = sibling.data
    report = module.simplify_materials([obj], ['Stocking Main'])
    replacement = obj.material_slots[0].material
    assert replacement != source and replacement.name == 'Stocking Main'
    assert sibling.data == original_data and sibling.material_slots[0].material == source
    assert obj.data != sibling.data
    assert obj.material_slots[1].material == other
    assert graph(source) == before
    assert [tuple(vertex.co) for vertex in obj.data.vertices] == coordinates
    assert len(replacement.node_tree.nodes) == 2
    assert report[0]['approximated_inputs'] == ['Base Color']
    close(report[0]['base_color'], (0.018776, 0.018776, 0.018776, 1))
    count = len(bpy.data.materials)
    assert module.simplify_materials([obj], ['Stocking Main']) == report
    assert len(bpy.data.materials) == count
    assert module.simplify_materials([sibling], []) == []
    assert graph(sibling.material_slots[0].material) == before


def test_image_and_unsafe_mapping():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = mesh('Body')
    source, shader = material('Image Material')
    texture = source.node_tree.nodes.new('ShaderNodeTexImage')
    texture.image = bpy.data.images.new('Base', width=2, height=2)
    source.node_tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
    source.node_tree.links.new(texture.outputs['Alpha'], shader.inputs['Alpha'])
    obj.data.materials.append(source)
    report = module.simplify_materials([obj], ['Image Material'])[0]
    assert [entry['input'] for entry in report['retained_textures']] == ['Base Color', 'Alpha']
    assert not report['approximated_inputs']
    assert next(node for node in obj.active_material.node_tree.nodes if node.type == 'TEX_IMAGE').image == texture.image
    mapped, mapped_shader = material('Procedural Coordinates')
    mapped_image = mapped.node_tree.nodes.new('ShaderNodeTexImage')
    mapped_image.image = texture.image
    coordinates = mapped.node_tree.nodes.new('ShaderNodeTexCoord')
    mapped.node_tree.links.new(coordinates.outputs['Generated'], mapped_image.inputs['Vector'])
    mapped.node_tree.links.new(mapped_image.outputs['Color'], mapped_shader.inputs['Base Color'])
    obj.data.materials.append(mapped)
    report = module.simplify_materials([obj], ['Procedural Coordinates'])[0]
    assert not report['retained_textures'] and report['approximated_inputs'] == ['Base Color']


def test_group_not_unused_principled_and_missing_request():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = mesh('Object')
    source, shader = material('Mixed')
    source.diffuse_color = (0.12, 0.23, 0.34, 1)
    output = source.node_tree.nodes.get('Material Output')
    emission = source.node_tree.nodes.new('ShaderNodeEmission')
    source.node_tree.links.new(emission.outputs[0], output.inputs['Surface'])
    obj.data.materials.append(source)
    before = graph(source)
    try:
        module.simplify_materials([obj], ['Missing'])
    except ValueError:
        pass
    else:
        raise AssertionError('Missing requested material was silently ignored')
    assert obj.active_material == source and graph(source) == before
    result = module.simplify_materials([obj], ['Mixed'])[0]
    assert result['value_source'] == 'Material viewport defaults'
    close(result['base_color'], (0.12, 0.23, 0.34, 1))


def test_fbx_roundtrip_and_fresh_export_restoration():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = mesh('Stocking')
    source, shader = material()
    source.node_tree.links.new(source.node_tree.nodes.new('ShaderNodeTexMagic').outputs['Color'], shader.inputs['Base Color'])
    obj.data.materials.append(source)
    folder = Path(tempfile.mkdtemp(prefix='cdesigner_materials_test_'))
    original_file = folder / 'original.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(original_file))
    module.simplify_materials([obj], ['Stocking Main'])
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.fbx(filepath=str(folder / 'simple.fbx'), use_selection=True, object_types={'MESH'}, bake_anim=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(folder / 'simple.fbx'), use_anim=False)
    imported = bpy.data.materials['Stocking Main']
    imported_shader = imported.node_tree.nodes.get('Principled BSDF')
    close(imported_shader.inputs['Base Color'].default_value, (0.018776, 0.018776, 0.018776, 1), 2e-5)
    close([imported_shader.inputs['Roughness'].default_value, imported_shader.inputs['Metallic'].default_value], [0.5, 0])
    bpy.ops.wm.open_mainfile(filepath=str(original_file), load_ui=False, use_scripts=False)
    original = bpy.data.materials['Stocking Main']
    assert any(node.type == 'TEX_MAGIC' for node in original.node_tree.nodes)
    assert module.simplify_materials([bpy.data.objects['Stocking']], []) == []
    assert bpy.data.objects['Stocking'].active_material == original


if __name__ == '__main__':
    for test in (test_source_slots_and_idempotence, test_image_and_unsafe_mapping,
                 test_group_not_unused_principled_and_missing_request,
                 test_fbx_roundtrip_and_fresh_export_restoration):
        test()
        print('PASS', test.__name__)
    print('Unity simple materials: 4 test groups passed')
