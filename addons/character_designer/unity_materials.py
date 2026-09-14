"""Small, explicit material approximations for the disposable Unity export.

This module does not register an add-on. Never call it on the artist's live
scene: the isolated export worker owns every object passed here. Original
material node trees are retained, and material names are transferred to their
export replacements so repeated FBX exports keep the same material identity.
"""

from __future__ import annotations

import json
import math

import bpy


_SUMMARY = '_cdesigner_simple_export_material'


def _incoming(socket):
    """Resolve only reroutes; never guess how a group or shader mix evaluates."""
    seen = set()
    while socket is not None and socket.is_linked and len(socket.links) == 1:
        source = socket.links[0].from_socket
        node = source.node
        if node.type != 'REROUTE':
            return source
        if node.as_pointer() in seen:
            return None
        seen.add(node.as_pointer())
        socket = node.inputs[0]
    return None


def _principled(material):
    if not material.use_nodes or material.node_tree is None:
        return None
    outputs = [node for node in material.node_tree.nodes
               if node.type == 'OUTPUT_MATERIAL' and node.is_active_output]
    # Target-specific output graphs can disagree. Use a single common output.
    common = [node for node in outputs if node.target == 'ALL']
    output = common[0] if len(common) == 1 else (outputs[0] if len(outputs) == 1 else None)
    source = _incoming(output.inputs.get('Surface')) if output else None
    return source.node if source is not None and source.node.type == 'BSDF_PRINCIPLED' else None


def _finite(value, fallback, minimum=0.0, maximum=1.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    return min(maximum, max(minimum, value)) if math.isfinite(value) else float(fallback)


def _base_image(socket):
    """Only an ordinary image in the default UV space is safely transferable."""
    source = _incoming(socket)
    if source is None or source.node.type != 'TEX_IMAGE' or source.name != 'Color':
        return None
    node = source.node
    if (node.image is None or node.image.source not in {'FILE', 'GENERATED'}
            or node.projection != 'FLAT'):
        return None
    vector = node.inputs.get('Vector')
    coordinates = _incoming(vector)
    if vector.is_linked and not (coordinates is not None
                                and coordinates.node.type == 'TEX_COORD'
                                and coordinates.name == 'UV'):
        return None
    return node


def _make_simple(original, export_name):
    shader = _principled(original)
    inputs = shader.inputs if shader else None
    base = list(inputs['Base Color'].default_value if shader else original.diffuse_color)
    base = [_finite(value, 0.8 if index < 3 else 1.0, maximum=1e6 if index < 3 else 1.0)
            for index, value in enumerate(base)]
    roughness = _finite(inputs['Roughness'].default_value if shader else original.roughness, 0.5)
    metallic = _finite(inputs['Metallic'].default_value if shader else original.metallic, 0.0)
    alpha = _finite(inputs['Alpha'].default_value if shader else base[3], 1.0)
    image_node = _base_image(inputs['Base Color']) if shader else None
    retained = []
    omitted = []
    if shader:
        for socket in inputs:
            if socket.is_linked:
                omitted.append(socket.name)
    else:
        omitted.append('Custom surface shader')

    # Material.copy also preserves render settings. Node links, drivers and
    # procedural nodes belong to the original only, never the approximation.
    material = original.copy()
    material.animation_data_clear()
    material.use_nodes = True
    material.node_tree.animation_data_clear()
    nodes = material.node_tree.nodes
    nodes.clear()
    simple = nodes.new('ShaderNodeBsdfPrincipled')
    output = nodes.new('ShaderNodeOutputMaterial')
    output.is_active_output = True
    material.node_tree.links.new(simple.outputs['BSDF'], output.inputs['Surface'])
    simple.inputs['Base Color'].default_value = base
    simple.inputs['Roughness'].default_value = roughness
    simple.inputs['Metallic'].default_value = metallic
    simple.inputs['Alpha'].default_value = alpha
    material.diffuse_color = (*base[:3], alpha)
    material.roughness = roughness
    material.metallic = metallic
    if image_node:
        texture = nodes.new('ShaderNodeTexImage')
        texture.image = image_node.image
        texture.interpolation = image_node.interpolation
        texture.extension = image_node.extension
        material.node_tree.links.new(texture.outputs['Color'], simple.inputs['Base Color'])
        retained.append({'input': 'Base Color', 'image': texture.image.name, 'coordinates': 'UV'})
        omitted.remove('Base Color')
        # Sharing this image's Alpha output is safe; unrelated masks are not.
        alpha_source = _incoming(inputs['Alpha'])
        if alpha_source is not None and alpha_source.node == image_node and alpha_source.name == 'Alpha':
            material.node_tree.links.new(texture.outputs['Alpha'], simple.inputs['Alpha'])
            retained.append({'input': 'Alpha', 'image': texture.image.name, 'coordinates': 'UV'})
            omitted.remove('Alpha')
    summary = {
        'material': export_name,
        'method': 'simple_principled',
        'base_color': base,
        'roughness': roughness,
        'metallic': metallic,
        'alpha': alpha,
        'value_source': 'Principled defaults' if shader else 'Material viewport defaults',
        'retained_textures': retained,
        'approximated_inputs': sorted(set(omitted)),
        'note': ('Export-only approximation; procedural patterns, shader groups and advanced '
                 'surface effects are not translated. The Blender material remains unchanged.'),
    }
    material[_SUMMARY] = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    return material, summary


def simplify_materials(objects, names):
    """Replace requested materials in snapshot meshes; return JSON-safe summaries.

    Names are exact original material names captured with the job. An unknown
    name fails instead of silently exporting a shader the user asked to replace.
    Calling twice on the same disposable objects is idempotent. Omit a name on
    the next fresh export to restore the original material automatically.
    """
    requested = set(names)
    if not requested:
        return []
    if any(not isinstance(name, str) or not name for name in requested):
        raise ValueError('Simple export material names must be non-empty strings.')
    objects = list(dict.fromkeys(obj for obj in objects if obj.type == 'MESH'))
    materials = {slot.material.name: slot.material for obj in objects
                 for slot in obj.material_slots if slot.material is not None}
    missing = requested - set(materials)
    if missing:
        raise ValueError('Requested simple export materials are missing: ' + ', '.join(sorted(missing)))
    if any(obj.library or obj.data.library for obj in objects):
        raise ValueError('Simple materials require local disposable export meshes.')
    result = []
    replacements = {}
    for name in sorted(requested):
        original = materials[name]
        if original.get(_SUMMARY):
            result.append(json.loads(original[_SUMMARY]))
            continue
        replacement, summary = _make_simple(original, name)
        replacements[original] = replacement
        result.append(summary)
    for obj in objects:
        if not any(slot.material in replacements for slot in obj.material_slots):
            continue
        # A snapshot may still contain mesh instances outside this export list.
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        for slot in obj.material_slots:
            if slot.material in replacements:
                slot.material = replacements[slot.material]
    for original, replacement in replacements.items():
        name = original.name
        # Names are changed only in this throwaway Blender process. Renaming the
        # original avoids .001 suffixes and keeps Unity material remaps stable.
        original.name = '.CDesigner Source Material ' + name
        replacement.name = name
        if replacement.name != name:
            raise ValueError(f'Could not preserve export material identity: {name}')
    return result
