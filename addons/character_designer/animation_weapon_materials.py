"""Build lightweight Principled previews from Unity's weapon manifest."""

from array import array
import hashlib
import math
import re

import bpy

SHA = re.compile(r'[0-9a-f]{64}')
TEXTURE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tga'}
NEUTRAL = {'name': 'Weapon Surface', 'color': [0.8, 0.8, 0.8, 1.0],
           'emission': [0.0, 0.0, 0.0], 'metallic': 0.0, 'roughness': 0.5, 'transparent': False}


def _numbers(values, size):
    if (not isinstance(values, list) or len(values) != size or
            not all(type(value) in (int, float) and math.isfinite(value) for value in values)):
        raise ValueError('Invalid Unity weapon material values.')
    return values


def _image(record, folder, images):
    checksum, extension = record['sha256'], record['extension']
    if not SHA.fullmatch(checksum) or extension not in TEXTURE_EXTENSIONS:
        raise ValueError('Invalid Unity weapon texture identity.')
    key = checksum, record['srgb']
    if key not in images:
        path = folder / 'weapon-textures' / (checksum + extension)
        if path.stat().st_size > 32 * 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError('A Unity weapon texture copy changed. Export the library again.')
        image = bpy.data.images.load(str(path), check_existing=False)
        images[key] = image
        image.colorspace_settings.name = 'sRGB' if record['srgb'] else 'Non-Color'
        image.pack()
    return images[key]


def _texture(tree, record, folder, images, y):
    tex = tree.nodes.new('ShaderNodeTexImage')
    tex.location = (-520, y)
    tex.image = _image(record, folder, images)
    scale, offset = _numbers(record['scale'], 2), _numbers(record['offset'], 2)
    if scale != [1, 1] or offset != [0, 0]:
        uv = tree.nodes.new('ShaderNodeTexCoord')
        uv.location = (-960, y)
        mapping = tree.nodes.new('ShaderNodeMapping')
        mapping.location = (-750, y)
        mapping.inputs['Scale'].default_value = (*scale, 1)
        mapping.inputs['Location'].default_value = (*offset, 0)
        tree.links.new(uv.outputs['UV'], mapping.inputs['Vector'])
        tree.links.new(mapping.outputs['Vector'], tex.inputs['Vector'])
    return tex


def _tint(tree, source, color, target, y):
    if tuple(color[:3]) == (1.0, 1.0, 1.0):
        tree.links.new(source, target)
        return
    tint = tree.nodes.new('ShaderNodeMixRGB')
    tint.label = 'Unity Color'
    tint.blend_type = 'MULTIPLY'
    tint.location = (-220, y)
    tint.inputs[0].default_value = 1.0
    tint.inputs[2].default_value = (*color[:3], 1.0)
    tree.links.new(source, tint.inputs[1])
    tree.links.new(tint.outputs[0], target)


def _create_material(spec, has_uv, folder, materials, images):
    color = _numbers(spec['color'], 4)
    emission = _numbers(spec['emission'], 3)
    metallic, roughness = _numbers([spec['metallic'], spec['roughness']], 2)
    material = bpy.data.materials.new(spec['name'] or 'Weapon Surface')
    materials.append(material)
    material.use_nodes = True
    material.diffuse_color = tuple(color)
    material.metallic = metallic
    material.roughness = roughness
    tree = material.node_tree
    bsdf = next(node for node in tree.nodes if node.type == 'BSDF_PRINCIPLED')
    bsdf.inputs['Base Color'].default_value = color
    bsdf.inputs['Metallic'].default_value = metallic
    bsdf.inputs['Roughness'].default_value = roughness
    strength = max(1.0, *emission)
    light = tuple(value / strength for value in emission)
    bsdf.inputs['Emission Color'].default_value = (*light, 1.0)
    bsdf.inputs['Emission Strength'].default_value = strength if any(light) else 0.0
    albedo = spec.get('albedo') if has_uv else None
    if albedo:
        tex = _texture(tree, albedo, folder, images, 360)
        _tint(tree, tex.outputs['Color'], color, bsdf.inputs['Base Color'], 360)
    if spec['transparent']:
        bsdf.inputs['Alpha'].default_value = color[3]
        if albedo:
            alpha = tree.nodes.new('ShaderNodeMath')
            alpha.operation = 'MULTIPLY'
            alpha.location = (-220, 80)
            alpha.inputs[1].default_value = color[3]
            tree.links.new(tex.outputs['Alpha'], alpha.inputs[0])
            tree.links.new(alpha.outputs[0], bsdf.inputs['Alpha'])
    if has_uv and spec.get('emissive') and any(light):
        tex = _texture(tree, spec['emissive'], folder, images, -220)
        _tint(tree, tex.outputs['Color'], light, bsdf.inputs['Emission Color'], -220)
    return material


def apply(folder, records, objects, appearance):
    """Add UVs and preview materials to one verified collection of blank meshes."""
    if not isinstance(appearance, list) or len(appearance) != len(records):
        raise ValueError('Unity weapon appearance does not match its visible meshes.')
    material_cache, images, created = {}, {}, []
    changed = []
    try:
        for captured, obj, preview in zip(records, objects, appearance):
            mesh = obj.data
            uv = preview['uv']
            if uv is not None:
                _numbers(uv, len(mesh.vertices) * 2)
            slots = preview['slots']
            submeshes = captured['submeshes']
            if not isinstance(slots, list) or not slots or len(slots) != len(submeshes):
                raise ValueError('Unity weapon material slots do not match its submeshes.')
            changed.append(mesh)
            if uv is not None:
                layer = mesh.uv_layers.new(name='UVMap')
                coords = array('f', (uv[loop.vertex_index * 2 + axis] for loop in mesh.loops for axis in (0, 1)))
                layer.uv.foreach_set('vector', coords)
            if not any(slots):
                continue
            indices = array('i')
            for index, (spec, submesh) in enumerate(zip(slots, submeshes)):
                if (submesh['submesh'] != index or submesh['triangleStart'] != len(indices) or
                        type(submesh['triangleCount']) is not int or submesh['triangleCount'] < 0 or
                        len(indices) + submesh['triangleCount'] > len(mesh.polygons)):
                    raise ValueError('Invalid Unity weapon submesh ranges.')
                indices.extend([index] * submesh['triangleCount'])
                spec = spec or NEUTRAL
                key = (repr(spec), uv is not None)
                if key not in material_cache:
                    material_cache[key] = _create_material(spec, uv is not None, folder, created, images)
                mesh.materials.append(material_cache[key])
            if len(indices) != len(mesh.polygons):
                raise ValueError('Unity weapon submeshes do not cover all faces.')
            mesh.polygons.foreach_set('material_index', indices)
            mesh.update()
    except Exception:
        for mesh in reversed(changed):
            mesh.materials.clear()
            if mesh.uv_layers:
                mesh.uv_layers.remove(mesh.uv_layers[-1])
            mesh.update()
        for material in created:
            if not material.users:
                bpy.data.materials.remove(material)
        for image in images.values():
            if not image.users:
                bpy.data.images.remove(image)
        raise
