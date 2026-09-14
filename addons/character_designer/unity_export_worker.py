"""Isolated Blender worker for a rest-pose Unity model export.

This module is deliberately independent of add-on registration. It only edits
the disposable .blend snapshot passed to a separate Blender process.
"""

from __future__ import annotations

import hashlib
import json
from array import array
from pathlib import Path
import re
import shutil
import sys
import traceback

import bpy
from mathutils import Matrix


CONTROL_OWNERS = {
    'limb_ik', 'foot_controls', 'torso_controls', 'eye_controls',
    'spine_ik_fk', 'root_control',
}
SUPPORTED_MODIFIERS = {'MIRROR', 'SUBSURF', 'SOLIDIFY', 'TRIANGULATE', 'NODES'}


class ExportError(ValueError):
    pass


def _warn(warnings, message):
    if message not in warnings:
        warnings.append(message)


def _clear_animation(block):
    if block is not None and getattr(block, 'animation_data', None):
        block.animation_data_clear()


def _coordinates(items):
    values = array('f', [0.0]) * (3 * len(items))
    items.foreach_get('co', values)
    return values


def _topology(mesh):
    """Validate connectivity, not just the number of evaluated vertices."""
    digest = hashlib.sha256()
    for collection, attribute, width in ((mesh.edges, 'vertices', 2),
                                          (mesh.loops, 'vertex_index', 1),
                                          (mesh.polygons, 'loop_total', 1)):
        values = array('i', [0]) * (width * len(collection))
        collection.foreach_get(attribute, values)
        digest.update(values.tobytes())
    return len(mesh.vertices), len(mesh.edges), len(mesh.polygons), digest.digest()


def _same_geometry_and_weights(left, right):
    if _topology(left) != _topology(right):
        return False
    left_coordinates, right_coordinates = _coordinates(left.vertices), _coordinates(right.vertices)
    tolerance = 1e-6 * max(1.0, max((abs(value) for value in left_coordinates), default=0.0))
    if any(abs(a - b) > tolerance for a, b in zip(left_coordinates, right_coordinates)):
        return False
    for a, b in zip(left.vertices, right.vertices):
        first = {group.group: group.weight for group in a.groups}
        second = {group.group: group.weight for group in b.groups}
        if set(first) != set(second) or any(abs(weight - second[index]) > 1e-7 for index, weight in first.items()):
            return False
    return True


def _shape_inputs(obj, owned_names):
    keys = obj.data.shape_keys
    if keys is None:
        if owned_names:
            raise ExportError(f'{obj.name}: a recorded calibration Shape Key is missing.')
        return _coordinates(obj.data.vertices), []
    if not keys.use_relative:
        raise ExportError(f'{obj.name}: absolute Shape Keys need conversion to relative keys before Unity export.')
    missing = set(owned_names) - set(keys.key_blocks.keys())
    if missing:
        raise ExportError(f'{obj.name}: missing owned Shape Keys: {", ".join(sorted(missing))}.')
    owned = {keys.key_blocks[name] for name in owned_names}
    artists = [key for key in keys.key_blocks if key != keys.reference_key and key not in owned]
    for key in artists:
        if key.relative_key in owned:
            raise ExportError(f'{obj.name}: artist Shape Key "{key.name}" depends on an omitted calibration key.')
    basis = _coordinates(keys.reference_key.data)
    result = []
    for key in artists:
        current = _coordinates(key.data)
        relative = _coordinates(key.relative_key.data)
        weights = None
        if key.vertex_group:
            group = obj.vertex_groups.get(key.vertex_group)
            if group is None:
                raise ExportError(f'{obj.name}: Shape Key "{key.name}" has a missing vertex-group mask.')
            weights = [next((entry.weight for entry in vertex.groups if entry.group == group.index), 0.0)
                       for vertex in obj.data.vertices]
        # FBX exports relative deltas but ignores Blender's key vertex-group
        # masks. Flatten each delta into Basis and bake that mask explicitly.
        coordinates = array('f', (base + (a - b) * (weights[index // 3] if weights is not None else 1.0)
                                  for index, (base, a, b) in enumerate(zip(basis, current, relative))))
        result.append({'name': key.name, 'coordinates': coordinates,
                       'value': float(key.value) if not key.mute else 0.0,
                       'slider_min': key.slider_min, 'slider_max': key.slider_max,
                       'interpolation': key.interpolation})
    return basis, result


def _bake_mesh(context, obj, owned_names, warnings, forearm=None):
    """Evaluate each artist delta with the same modifier stack, keeping weights."""
    basis, shapes = _shape_inputs(obj, owned_names)
    applied = []
    disabled_skinning = []
    for modifier in obj.modifiers:
        if modifier.type == 'ARMATURE':
            if not modifier.show_viewport:
                disabled_skinning.append(modifier.name)
                _warn(warnings, f'{obj.name}: disabled Armature modifier "{modifier.name}" was omitted; this part will not deform with that rig.')
                continue
            if modifier.object is None:
                raise ExportError(f'{obj.name}: an Armature modifier has no rig.')
            if not modifier.use_vertex_groups or modifier.use_bone_envelopes:
                raise ExportError(f'{obj.name}: Unity export requires vertex-group skinning without bone envelopes.')
            if modifier.vertex_group or modifier.use_multi_modifier:
                raise ExportError(f'{obj.name}: masked or multi-modifier Armature blending cannot be represented by one Unity skin.')
            if modifier.use_deform_preserve_volume:
                _warn(warnings, f'{obj.name}: Blender Preserve Volume skinning is exported as standard FBX skin weights; review joint deformation in Unity.')
            continue
        if not modifier.show_viewport:
            continue
        if modifier.type not in SUPPORTED_MODIFIERS:
            raise ExportError(f'{obj.name}: enabled {modifier.type} modifier "{modifier.name}" is not supported by the safe export baker.')
        applied.append(modifier.type)
    armatures = [modifier for modifier in obj.modifiers if modifier.type == 'ARMATURE' and modifier.show_viewport]
    if len(armatures) > 1:
        raise ExportError(f'{obj.name}: more than one active Armature modifier needs an explicit combined skinning setup.')
    temporary = obj.copy()
    temporary.data = obj.data.copy()
    temporary.name = '.CDesigner Export Evaluation'
    # Snapshot ownership must never make the calibration handler claim this
    # disposable evaluation object if another add-on is present in the worker.
    for name in tuple(temporary.keys()):
        if name.startswith('character_designer_'):
            del temporary[name]
    context.scene.collection.objects.link(temporary)
    _clear_animation(temporary)
    _clear_animation(temporary.data.shape_keys)
    if temporary.data.shape_keys:
        temporary.shape_key_clear()
    for modifier in tuple(temporary.modifiers):
        if modifier.type == 'ARMATURE' or not modifier.show_viewport:
            temporary.modifiers.remove(modifier)
    for constraint in tuple(temporary.constraints):
        temporary.constraints.remove(constraint)
    base_mesh = None
    try:
        def evaluate(coordinates):
            temporary.data.vertices.foreach_set('co', coordinates)
            temporary.data.update()
            context.view_layer.update()
            depsgraph = context.evaluated_depsgraph_get()
            depsgraph.update()
            evaluated = temporary.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
            try:
                for modifier in temporary.modifiers:
                    if modifier.type != 'NODES':
                        continue
                    comparison = None
                    try:
                        modifier.show_viewport = False
                        context.view_layer.update()
                        depsgraph.update()
                        without = temporary.evaluated_get(depsgraph)
                        comparison = bpy.data.meshes.new_from_object(without, preserve_all_data_layers=True, depsgraph=depsgraph)
                        if not _same_geometry_and_weights(mesh, comparison):
                            raise ExportError(f'{obj.name}: Geometry Nodes modifier "{modifier.name}" changes geometry or skin weights; only verified normal/attribute-only nodes can be exported safely.')
                    finally:
                        modifier.show_viewport = True
                        if comparison is not None:
                            bpy.data.meshes.remove(comparison)
                return mesh
            except Exception:
                bpy.data.meshes.remove(mesh)
                raise

        base_mesh = evaluate(basis)
        expected = _topology(base_mesh)
        correction = None
        if forearm is not None:
            import importlib.util
            spec = importlib.util.spec_from_file_location('cdesigner_unity_forearm', Path(__file__).with_name('unity_forearm.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            correction = module.bake(forearm, temporary, evaluate, basis, base_mesh)
        for shape in shapes:
            mesh = evaluate(shape.pop('coordinates'))
            try:
                if _topology(mesh) != expected:
                    raise ExportError(f'{obj.name}: "{shape["name"]}" changes modifier topology; export cannot safely preserve its BlendShape correspondence.')
                shape['coordinates'] = _coordinates(mesh.vertices)
            finally:
                bpy.data.meshes.remove(mesh)
        obj.data = base_mesh
        base_mesh.name = obj.name + ' Mesh'
        base_mesh = None
        for modifier in tuple(obj.modifiers):
            if modifier.type != 'ARMATURE' or not modifier.show_viewport:
                obj.modifiers.remove(modifier)
        if shapes:
            base = obj.shape_key_add(name='Basis', from_mix=False)
            for shape in shapes:
                key = obj.shape_key_add(name=shape['name'], from_mix=False)
                key.data.foreach_set('co', shape['coordinates'])
                key.relative_key = base
                key.slider_min = min(shape['slider_min'], key.slider_max)
                key.slider_max = max(shape['slider_max'], key.slider_min)
                key.value = shape['value']
                key.interpolation = shape['interpolation']
            obj.data.shape_keys.use_relative = True
        _clear_animation(obj.data)
        if owned_names and forearm is None:
            _warn(warnings, 'Blender-only forearm calibration corrections were omitted; Unity does not run their Blender handler.')
        return {'vertices': len(obj.data.vertices), 'polygons': len(obj.data.polygons),
                'shape_keys': [shape['name'] for shape in shapes], 'modifiers_baked': applied,
                'skinned': bool(armatures), 'disabled_skinning': disabled_skinning,
                **({'forearm': correction} if correction else {})}
    finally:
        data = temporary.data
        bpy.data.objects.remove(temporary, do_unlink=True)
        if data.users == 0:
            bpy.data.meshes.remove(data)
        if base_mesh is not None and base_mesh.users == 0:
            bpy.data.meshes.remove(base_mesh)


def _is_control(bone):
    if bone.get('character_designer_owner') in CONTROL_OWNERS:
        return True
    if bone.use_deform:
        return False
    name = bone.name.rsplit(':', 1)[-1].upper()
    return name.startswith(('CTRL_', 'CTRL-', 'MCH_', 'MCH-', 'ORG_', 'ORG-', 'ORI_', 'ORI-'))


def _clean_skeleton(context, obj, objects):
    accessory = bool(obj.get('character_designer_skirt_owner')
                     or obj.get('character_designer_hair_bones_owner')
                     or obj.get('character_designer_hair_variant_version'))
    names = {bone.name for bone in obj.data.bones if not _is_control(bone)
             and (not accessory or bone.use_deform)}
    if not any(obj.data.bones[name].use_deform for name in names):
        raise ExportError(f'{obj.name}: no deform skeleton remains after filtering control bones.')
    weighted_meshes = [mesh for mesh in objects if mesh.type == 'MESH'
                       and any(mod.type == 'ARMATURE' and mod.object == obj for mod in mesh.modifiers)]
    for mesh in weighted_meshes:
        for bone in obj.data.bones:
            group = mesh.vertex_groups.get(bone.name)
            if group is None:
                continue
            if bone.use_deform and bone.name not in names:
                if any(any(entry.group == group.index and entry.weight > 1e-8 for entry in vertex.groups)
                       for vertex in mesh.data.vertices):
                    raise ExportError(f'{mesh.name}: generated control "{bone.name}" has actual skin weights and cannot be discarded safely.')
            elif not bone.use_deform:
                # FBX can otherwise turn an inert Blender vertex group into an
                # active skin cluster for a retained, non-deforming ancestor.
                mesh.vertex_groups.remove(group)
    # Preserve explicit non-deforming object attachment targets, unless they
    # are generated mechanisms. Generated targets are converted to object
    # parenting below while preserving their evaluated rest world transform.
    for attached in objects:
        if attached.parent == obj and attached.parent_type == 'BONE':
            bone = obj.data.bones.get(attached.parent_bone)
            if bone is not None and not _is_control(bone) and (not accessory or bone.use_deform):
                names.add(bone.name)
    for attached in objects:
        if attached.parent == obj and attached.parent_type == 'BONE' and attached.parent_bone not in names:
            world = attached.matrix_world.copy()
            attached.parent_type = 'OBJECT'
            attached.parent_bone = ''
            attached.matrix_world = world
    for candidate in context.view_layer.objects:
        candidate.select_set(False)
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.hide_select = False
    obj.select_set(True)
    context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        matrices = {bone.name: bone.matrix.copy() for bone in obj.data.edit_bones if bone.name in names}
        lengths = {bone.name: bone.length for bone in obj.data.edit_bones if bone.name in names}
        parents = {}
        for name in names:
            parent = obj.data.edit_bones[name].parent
            while parent is not None and parent.name not in names:
                parent = parent.parent
            parents[name] = parent.name if parent else None
        for name in names:
            bone = obj.data.edit_bones[name]
            bone.use_connect = False
            bone.parent = obj.data.edit_bones.get(parents[name]) if parents[name] else None
        for bone in tuple(obj.data.edit_bones):
            if bone.name not in names:
                obj.data.edit_bones.remove(bone)
        for name in names:
            bone = obj.data.edit_bones[name]
            bone.matrix = matrices[name]
            bone.length = lengths[name]
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    obj.data.pose_position = 'REST'
    for pose_bone in obj.pose.bones:
        pose_bone.custom_shape = None
        pose_bone.matrix_basis = Matrix.Identity(4)
    return sorted(names)


def _material_images(material):
    result, seen = set(), set()
    def visit(tree):
        if tree is None or tree.as_pointer() in seen:
            return
        seen.add(tree.as_pointer())
        for node in tree.nodes:
            image = getattr(node, 'image', None)
            if image is not None:
                result.add(image)
            visit(getattr(node, 'node_tree', None))
    visit(material.node_tree if material.use_nodes else None)
    return result


def _merge_accessory_skeletons(context, main, objects):
    """Flatten accessory bones into the disposable export skeleton only.

    Nested Armature nodes produce ambiguous FBX skins (including a Blender
    importer failure). A single skeleton retains the Hips/Head attachment and
    avoids carrying Blender-specific nested rig objects into Unity.
    """
    auxiliaries = [obj for obj in objects if obj.type == 'ARMATURE' and obj != main]
    if not auxiliaries:
        return objects, {}
    def depth(obj):
        result, parent = 0, obj.parent
        while parent is not None:
            result += 1
            parent = parent.parent
        return result
    auxiliaries.sort(key=lambda obj: (depth(obj), obj.name))
    used = set(main.data.bones.keys())
    mappings, plans = {}, []
    for rig in auxiliaries:
        transform = main.matrix_world.inverted() @ rig.matrix_world
        axes = [transform.to_3x3().col[index] for index in range(3)]
        lengths = [axis.length for axis in axes]
        if (min(lengths) <= 1e-9 or transform.to_3x3().determinant() <= 0
                or max(lengths) - min(lengths) > 1e-5 * max(lengths)
                or any(abs(axes[a].dot(axes[b])) > 1e-5 * lengths[a] * lengths[b]
                       for a, b in ((0, 1), (0, 2), (1, 2)))):
            raise ExportError(f'{rig.name}: a mirrored, sheared or non-uniform accessory rig transform cannot be flattened safely.')
        meshes = [obj for obj in objects if obj.type == 'MESH'
                  and any(mod.type == 'ARMATURE' and mod.object == rig for mod in obj.modifiers)]
        mapping = {}
        for bone in rig.data.bones:
            name = bone.name if bone.name not in used else f'{rig.name}__{bone.name}'
            candidate, index = name, 1
            while (candidate in used or any(candidate != bone.name and mesh.vertex_groups.get(candidate) is not None
                                            for mesh in meshes)):
                candidate, index = f'{name}_{index:03}', index + 1
            mapping[bone.name] = candidate
            used.add(candidate)
        mappings[rig.name] = mapping
        attachment = None
        if rig.parent_type == 'BONE' and rig.parent is not None:
            if rig.parent == main:
                attachment = rig.parent_bone
            elif rig.parent.name in mappings:
                attachment = mappings[rig.parent.name].get(rig.parent_bone)
        if attachment is not None and attachment not in used:
            raise ExportError(f'{rig.name}: its attachment bone is not in the export skeleton.')
        bones = [{'name': mapping[bone.name],
                  'head': transform @ bone.head_local, 'tail': transform @ bone.tail_local,
                  'z_axis': (transform.to_3x3() @ bone.matrix_local.to_3x3().col[2]).normalized(),
                  'parent': mapping[bone.parent.name] if bone.parent else attachment,
                  'deform': bone.use_deform, 'inherit_scale': bone.inherit_scale}
                 for bone in rig.data.bones]
        plans.append((rig, meshes, mapping, bones))
    for obj in context.view_layer.objects:
        obj.select_set(False)
    main.select_set(True)
    context.view_layer.objects.active = main
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        for _, _, _, bones in plans:
            for state in bones:
                bone = main.data.edit_bones.new(state['name'])
                bone.head, bone.tail = state['head'], state['tail']
                bone.align_roll(state['z_axis'])
                bone.use_deform = state['deform']
                bone.inherit_scale = state['inherit_scale']
            for state in bones:
                bone = main.data.edit_bones[state['name']]
                bone.parent = main.data.edit_bones.get(state['parent']) if state['parent'] else None
                bone.use_connect = False
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    for rig, meshes, mapping, _ in plans:
        for mesh in meshes:
            for old_name, name in mapping.items():
                group = mesh.vertex_groups.get(old_name)
                if group is not None:
                    group.name = name
            for modifier in mesh.modifiers:
                if modifier.type == 'ARMATURE' and modifier.object == rig:
                    modifier.object = main
        for child in objects:
            if child.parent == rig:
                world = child.matrix_world.copy()
                parent_bone = mapping.get(child.parent_bone) if child.parent_type == 'BONE' else None
                child.parent = main
                child.parent_type = 'BONE' if parent_bone else 'OBJECT'
                child.parent_bone = parent_bone or ''
                child.matrix_world = world
    return [obj for obj in objects if obj not in auxiliaries], mappings


def _unweighted_vertices(obj):
    """Read-only diagnostic on the final export skin, after bone remapping."""
    armatures = [modifier.object for modifier in obj.modifiers
                 if modifier.type == 'ARMATURE' and modifier.show_viewport and modifier.object is not None]
    if not armatures:
        return None
    deform_names = {bone.name for rig in armatures for bone in rig.data.bones if bone.use_deform}
    indices = {group.index for group in obj.vertex_groups if group.name in deform_names}
    return sum(not any(entry.group in indices and entry.weight > 1e-8 for entry in vertex.groups)
               for vertex in obj.data.vertices)


def _readable_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')
    return value[:90] or 'Texture'


def _restore_image_buffers(job):
    """Restore unsaved texture painting captured read-only by the coordinator."""
    for name, record in job.get('image_buffers', {}).items():
        candidates = [image for image in bpy.data.images if image.name_full == name or image.name == name]
        if len(candidates) != 1:
            raise ExportError(f'Painted image "{name}" is missing or ambiguous in the snapshot.')
        old = candidates[0]
        width, height, count = int(record['width']), int(record['height']), int(record['count'])
        if width <= 0 or height <= 0 or count != width * height * 4:
            raise ExportError(f'Painted image "{name}" has invalid RGBA buffer dimensions.')
        path = Path(record['path'])
        if path.stat().st_size != count * 4:
            raise ExportError(f'Painted image "{name}" has an incomplete pixel snapshot.')
        pixels = array('f')
        with path.open('rb') as handle:
            pixels.fromfile(handle, count)
        replacement = bpy.data.images.new('.CDesigner Painted Image', width=width, height=height,
                                          alpha=True, float_buffer=bool(record.get('float', old.is_float)))
        replacement.colorspace_settings.name = old.colorspace_settings.name
        replacement.alpha_mode = old.alpha_mode
        replacement.filepath_raw = old.filepath
        replacement['_cd_unity_original_identity'] = old.name + '|' + (old.library.filepath if old.library else '')
        if old.source == 'FILE':
            replacement['_cd_unity_encoding_format'] = old.file_format
        replacement.pixels.foreach_set(pixels)
        replacement.update()
        original_name = old.name
        old.user_remap(replacement)
        bpy.data.images.remove(old)
        replacement.name = original_name


def _export_textures(objects, stage, warnings):
    materials = {slot.material for obj in objects if obj.type == 'MESH'
                 for slot in obj.material_slots if slot.material is not None}
    images = set()
    for material in materials:
        images.update(_material_images(material))
        if material.use_nodes:
            nodes = material.node_tree.nodes
            principled = any(node.type == 'BSDF_PRINCIPLED' for node in nodes)
            custom = any(node.type in {'GROUP', 'SHADER_TO_RGB', 'BSDF_TOON'} for node in nodes)
            procedural = any(node.type.startswith('TEX_') and node.type not in {'TEX_IMAGE', 'TEX_COORD'}
                             for node in nodes)
            if not principled or custom or procedural:
                _warn(warnings, f'Material "{material.name}" uses a custom shader; Unity needs a matching material/shader setup.')
    files = []
    folder = stage / 'Textures'
    for image in sorted(images, key=lambda item: item.name):
        if image.source not in {'FILE', 'GENERATED'}:
            _warn(warnings, f'Image "{image.name}" ({image.source}) was not copied; provide a single-file texture for Unity.')
            continue
        raw = bpy.path.abspath(image.filepath, library=image.library) if image.filepath else ''
        source = Path(raw) if raw else None
        packed = image.packed_file
        formats = {'PNG': '.png', 'JPEG': '.jpg', 'TARGA': '.tga', 'TARGA_RAW': '.tga',
                   'TIFF': '.tif', 'BMP': '.bmp', 'OPEN_EXR': '.exr', 'HDR': '.hdr',
                   'JPEG2000': '.jp2', 'WEBP': '.webp'}
        encode = image.source == 'GENERATED' or image.is_dirty
        encoded_format = image.get('_cd_unity_encoding_format', 'OPEN_EXR' if image.is_float else 'PNG')
        if encoded_format not in formats:
            encoded_format = 'OPEN_EXR' if image.is_float else 'PNG'
        raw_extension = source.suffix.lower() if source else ''
        known_extensions = set(formats.values()) | {'.jpeg', '.tiff', '.dds', '.psd'}
        extension = formats[encoded_format] if encode else (raw_extension if raw_extension in known_extensions
                                                           else formats.get(image.file_format, '.png'))
        if encode and ((encoded_format == 'JPEG' and raw_extension == '.jpeg')
                       or (encoded_format == 'TIFF' and raw_extension == '.tiff')):
            extension = raw_extension
        identity = image.get('_cd_unity_original_identity', image.name + '|' + (image.library.filepath if image.library else ''))
        suffix = hashlib.sha256(identity.encode('utf8')).hexdigest()[:8]
        stem = _readable_name(Path(image.name).stem)
        destination = folder / f'{stem}_{suffix}{extension}'
        folder.mkdir(parents=True, exist_ok=True)
        if encode:
            if image.source == 'GENERATED' and not image.has_data and len(image.pixels):
                # Generated pixels are loaded lazily in background Blender.
                _ = image.pixels[0]
            if not image.has_data:
                raise ExportError(f'Image "{image.name}" has no pixel data to export.')
            image.filepath_raw = str(destination)
            image.file_format = encoded_format
            image.save()
        elif packed is not None:
            destination.write_bytes(bytes(packed.data))
        elif source is not None and source.is_file():
            shutil.copy2(source, destination)
        else:
            raise ExportError(f'Texture "{image.name}" is missing: {raw or "no file path"}.')
        image.filepath = str(destination)
        files.append(destination.relative_to(stage).as_posix())
    return sorted(material.name for material in materials), files


def export_job(job):
    stage = Path(job['stage']).resolve()
    stage.mkdir(parents=True, exist_ok=True)
    filename = job['filename']
    if Path(filename).name != filename or not filename.lower().endswith('.fbx'):
        raise ExportError('The export filename must be one .fbx filename, without directories.')
    names = job['objects']
    missing = [name for name in names if bpy.data.objects.get(name) is None]
    if missing:
        raise ExportError('Snapshot objects are missing: ' + ', '.join(missing))
    objects = [bpy.data.objects[name] for name in names]
    if len(set(objects)) != len(objects):
        raise ExportError('The export object list contains duplicates.')
    main = bpy.data.objects.get(job['rig'])
    if main not in objects or main.type != 'ARMATURE':
        raise ExportError('The selected Main Rig is not in the export snapshot.')
    warnings = list(job.get('warnings', []))
    _restore_image_buffers(job)
    scene = bpy.data.scenes.new('CDesigner Unity Export')
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = float(job.get('unit_scale', 1.0))
    if scene.unit_settings.scale_length <= 0:
        raise ExportError('Scene unit scale must be positive.')
    bpy.context.window.scene = scene
    # Object dependencies can include Mirror reference empties and attachment
    # rigs. Link them for evaluation; FBX still exports only the explicit list.
    for obj in tuple(bpy.data.objects):
        scene.collection.objects.link(obj)
        _clear_animation(obj)
        _clear_animation(obj.data)
        if obj.type == 'ARMATURE':
            obj.data.pose_position = 'REST'
            for pose_bone in obj.pose.bones:
                pose_bone.matrix_basis = Matrix.Identity(4)
                for constraint in tuple(pose_bone.constraints):
                    pose_bone.constraints.remove(constraint)
    context = bpy.context
    context.view_layer.update()
    worlds = {obj: obj.matrix_world.copy() for obj in objects}
    origin = worlds[main].translation.copy()
    for obj in objects:
        if obj.type not in {'MESH', 'ARMATURE', 'EMPTY'}:
            raise ExportError(f'{obj.name}: only Mesh, Armature and attachment Empty objects are supported.')
        for constraint in tuple(obj.constraints):
            obj.constraints.remove(constraint)
        if obj.parent is not None and obj.parent not in objects:
            obj.parent = None
        obj.matrix_world = worlds[obj]
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_select = False
        obj.hide_render = False
    mesh_results = {}
    for obj in objects:
        if obj.type == 'MESH':
            for modifier in obj.modifiers:
                if modifier.type == 'ARMATURE' and modifier.show_viewport and modifier.object not in objects:
                    raise ExportError(f'{obj.name}: its skinning rig is not included in the export.')
            mesh_results[obj.name] = _bake_mesh(context, obj, job.get('owned_keys', {}).get(obj.name, []), warnings,
                                                job.get('forearm', {}).get(obj.name))
    source_rigs = {}
    for obj in objects:
        if obj.type == 'ARMATURE':
            source_rigs[obj.name] = _clean_skeleton(context, obj, objects)
    objects, accessory_mapping = _merge_accessory_skeletons(context, main, objects)
    rigs = {main.name: sorted(main.data.bones.keys())}
    for obj in objects:
        if obj.type == 'MESH':
            count = _unweighted_vertices(obj)
            mesh_results[obj.name]['unweighted_vertices'] = count
            if count:
                _warn(warnings, f'{obj.name}: {count} exported vertices have no weight on a retained deform bone; their existing weights were left unchanged.')
    # Move the complete export to one stable origin. Parent-first assignment
    # avoids applying the translation twice to attached objects.
    def depth(obj):
        count, parent = 0, obj.parent
        while parent is not None and parent in objects:
            count += 1
            parent = parent.parent
        return count
    translation = Matrix.Translation(-origin)
    for obj in sorted(objects, key=depth):
        obj.matrix_world = translation @ worlds[obj]
    context.view_layer.update()
    simple_materials = []
    if job.get('simple_materials'):
        import importlib.util
        spec = importlib.util.spec_from_file_location('cdesigner_unity_materials', Path(__file__).with_name('unity_materials.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        simple_materials = module.simplify_materials(objects, job['simple_materials'])
    materials, files = _export_textures(objects, stage, warnings)
    for obj in context.view_layer.objects:
        obj.select_set(obj in objects)
    context.view_layer.objects.active = main
    bpy.ops.preferences.addon_enable(module='io_scene_fbx')
    exported = bpy.ops.export_scene.fbx(
        filepath=str(stage / filename), use_selection=True,
        object_types={'ARMATURE', 'MESH', 'EMPTY'}, use_mesh_modifiers=False,
        use_armature_deform_only=False, add_leaf_bones=False,
        bake_anim=False, bake_anim_use_nla_strips=False, bake_anim_use_all_actions=False,
        axis_forward='-Z', axis_up='Y', primary_bone_axis='Y', secondary_bone_axis='X',
        apply_unit_scale=True, apply_scale_options='FBX_SCALE_UNITS', global_scale=1.0,
        bake_space_transform=False, armature_nodetype='NULL',
        path_mode='RELATIVE', embed_textures=False, use_custom_props=False,
    )
    if 'FINISHED' not in exported or not (stage / filename).is_file():
        raise ExportError('Blender did not complete the FBX export.')
    files.insert(0, filename)
    corrections = [info.pop('forearm') for info in mesh_results.values() if 'forearm' in info]
    forearm_info = None
    if corrections or job.get('had_forearm'):
        sidecar = Path(filename).stem + '.forearm.json'
        payload = {'schema': 'cdesigner.forearm/1', 'fbx': filename,
                   'fbxSha256': hashlib.sha256((stage / filename).read_bytes()).hexdigest(),
                   'meshes': corrections}
        (stage / sidecar).write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        files.append(sidecar)
        forearm_info = {'file': sidecar, 'meshes': [item['objectName'] for item in corrections],
                        'runtime': 'CharacterDesigner.Unity', 'max_angle_degrees': 120,
                        'status': ('Calibration exported; Unity companion must import the runtime prefab.' if corrections
                                   else 'Correction removed; Unity companion will restore the plain runtime prefab.')}
    return {'ok': True, 'files': files, 'warnings': warnings,
            'simple_materials': simple_materials,
            **({'forearm_correction': forearm_info} if forearm_info else {}),
            'objects': [obj.name for obj in objects], 'rigs': rigs,
            'source_rigs': source_rigs, 'accessory_bone_mapping': accessory_mapping,
            'meshes': mesh_results, 'shape_keys': {name: info['shape_keys'] for name, info in mesh_results.items()},
            'materials': materials, 'origin': list(origin), 'unit_scale': scene.unit_settings.scale_length}


def main():
    arguments = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if len(arguments) != 1:
        raise ExportError('Expected one export job JSON path after --.')
    job = json.loads(Path(arguments[0]).read_text(encoding='utf8'))
    result_path = Path(job['stage']) / 'result.json'
    try:
        result = export_job(job)
    except Exception as error:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({'ok': False, 'error': str(error), 'traceback': traceback.format_exc()},
                                         ensure_ascii=False, indent=2), encoding='utf8')
        raise
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print('CDESIGNER_UNITY_EXPORT_OK', json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
