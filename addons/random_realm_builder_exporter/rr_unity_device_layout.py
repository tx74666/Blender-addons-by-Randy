"""Explicit Unity device-layout import. Imported objects are reference-only.

No handlers, automatic imports, export identities, scene saves or selection operators.
The pure validation/conversion functions also work outside Blender.
"""

import json
import math
from pathlib import Path, PureWindowsPath
import struct
import textwrap
import uuid

try:
    import bpy
    from mathutils import Matrix
except ImportError:
    bpy = None
    Matrix = None


SCHEMA = "random-realm.unity-device-layout"
VERSION = 1
REFERENCE_PROP = "rr_unity_layout_reference"
REFERENCE_ID_PROP = "rr_unity_layout_reference_id"
DEVICE_ID_PROP = "rr_unity_layout_device_id"
OWNER_PROP = "rr_unity_layout_owner"
TOKEN_PROP = "rr_unity_layout_token"
ROLE_PROP = "rr_unity_layout_role"
PART_PROP = "rr_unity_layout_part"
OWNER = "RRUnityDeviceLayoutV1"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_DEVICES = 512
MAX_MESHES = 2048
MAX_VERTICES_PER_MESH = 250000
MAX_VERTICES_TOTAL = 1000000
MAX_PARTS_PER_DEVICE = 128
MAX_PARTS_TOTAL = 4096
MAX_MATERIALS = 2048
MAX_TRIANGLES_TOTAL = 2000000
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 16 * 1024 * 1024
MAX_IMAGES_TOTAL_BYTES = 64 * 1024 * 1024
MAX_IMAGES_TOTAL_PIXELS = 64 * 1024 * 1024
IDENTITY = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]


class LayoutValidationError(ValueError):
    pass


def _fail(label, message):
    raise LayoutValidationError(f"{label}: {message}")


def _text(value, label, empty=False):
    if not isinstance(value, str) or len(value) > 4096 or "\0" in value:
        _fail(label, "expected a bounded string")
    if not empty and not value.strip():
        _fail(label, "must not be empty")
    return value


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(label, "expected a number")
    if abs(value) > 1e12 or not math.isfinite(value):
        _fail(label, "expected a finite, bounded number")
    return float(value)


def _array(value, label, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        _fail(label, f"expected an array of at most {maximum} values")
    return value


def _record(value, label):
    if not isinstance(value, dict):
        _fail(label, "expected an object")
    return value


def _vector(value, label, size):
    values = _array(value, label, size)
    if len(values) != size:
        _fail(label, f"expected {size} values")
    return [_number(x, label) for x in values]


def _matrix(value, label):
    result = _vector(value, label, 16)
    if any(abs(result[12 + i] - IDENTITY[12 + i]) > 1e-6 for i in range(4)):
        _fail(label, "expected an affine row-major matrix")
    a, b, c, d, e, f, g, h, i = (result[n] for n in (0, 1, 2, 4, 5, 6, 8, 9, 10))
    determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if not math.isfinite(determinant) or abs(determinant) <= 1e-12:
        _fail(label, "matrix is not invertible")
    return result


def _png_path(value, base_directory, label):
    value = _text(value, label)
    relative = Path(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    if relative.is_absolute() or windows.is_absolute() or windows.drive or windows.root:
        _fail(label, "image path must be relative to the snapshot folder")
    if ":" in value or any(part == ".." for part in relative.parts):
        _fail(label, "image path must not traverse outside the snapshot folder")
    resolved = (base_directory / relative).resolve()
    if not resolved.is_relative_to(base_directory):
        _fail(label, "image path resolves outside the snapshot folder")
    if relative.suffix.lower() != ".png" or not resolved.is_file():
        _fail(label, "PNG is missing or has an unsupported extension")
    if resolved.stat().st_size > MAX_IMAGE_BYTES:
        _fail(label, "PNG exceeds the image size limit")
    with resolved.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        _fail(label, "file is not a PNG with an IHDR header")
    width, height = struct.unpack(">II", header[16:24])
    if not width or not height or width * height > MAX_IMAGE_PIXELS:
        _fail(label, "PNG dimensions exceed the preview limit")
    return str(resolved)


def validate_package(payload, base_directory):
    """Validate the whole snapshot before any Blender datablock is created."""
    payload = _record(payload, "snapshot")
    if payload.get("schema") != SCHEMA or type(payload.get("version")) is not int or payload["version"] != VERSION:
        _fail("snapshot", "unsupported schema/version")
    if payload.get("coordinateSpace") != "UNITY" or payload.get("units") != "meters":
        _fail("snapshot", "expected UNITY coordinates in meters")
    if "capturedInPlay" in payload and type(payload["capturedInPlay"]) is not bool:
        _fail("capturedInPlay", "expected a boolean")
    snapshot_id = _text(payload.get("snapshotId"), "snapshotId")
    try:
        uuid.UUID(snapshot_id)
    except ValueError:
        _fail("snapshotId", "expected a UUID")
    _text(payload.get("scenePath"), "scenePath", empty=True)
    base = Path(base_directory).resolve()
    if not base.is_dir():
        _fail("snapshot folder", "directory is missing")
    reference = dict(_record(payload.get("reference"), "reference"))
    for key in ("id", "name"):
        _text(reference.get(key), f"reference.{key}")
    for key in ("sourceStableId", "sourceBlend"):
        _text(reference.get(key), f"reference.{key}", empty=True)
    for key in ("authoringFrameInRoot", "worldMatrix"):
        reference[key] = _matrix(reference.get(key), f"reference.{key}")
    result = dict(payload, reference=reference)
    meshes, materials, devices = {}, {}, []
    vertices_total = triangles_total = parts_total = 0
    for index, source in enumerate(_array(payload.get("meshes"), "meshes", MAX_MESHES)):
        mesh = dict(_record(source, f"meshes[{index}]"))
        mesh_id = _text(mesh.get("id"), "mesh.id")
        if mesh_id in meshes:
            _fail("mesh.id", "duplicate identity")
        vertices = _array(mesh.get("vertices"), "mesh.vertices", MAX_VERTICES_PER_MESH * 3)
        if len(vertices) % 3:
            _fail("mesh.vertices", "expected flat XYZ values")
        mesh["vertices"] = [_number(x, "mesh.vertices") for x in vertices]
        count = len(vertices) // 3
        vertices_total += count
        if vertices_total > MAX_VERTICES_TOTAL:
            _fail("meshes", "total vertex limit exceeded")
        uv = _array(mesh.get("uv"), "mesh.uv", MAX_VERTICES_PER_MESH * 2)
        if uv and len(uv) != count * 2:
            _fail("mesh.uv", "expected one UV pair per vertex, or an empty array")
        mesh["uv"] = [_number(x, "mesh.uv") for x in uv]
        submeshes = []
        for submesh in _array(mesh.get("submeshes"), "mesh.submeshes", 128):
            indices = _array(_record(submesh, "submesh").get("triangles"), "submesh.triangles", MAX_TRIANGLES_TOTAL * 3)
            if len(indices) % 3 or any(type(x) is not int or x < 0 or x >= count for x in indices):
                _fail("submesh.triangles", "expected valid triangle vertex indices")
            triangles_total += len(indices) // 3
            if triangles_total > MAX_TRIANGLES_TOTAL:
                _fail("meshes", "total triangle limit exceeded")
            submeshes.append(dict(submesh, triangles=list(indices)))
        mesh["submeshes"] = submeshes
        meshes[mesh_id] = mesh
    for source in _array(payload.get("materials"), "materials", MAX_MATERIALS):
        material = dict(_record(source, "material"))
        material_id = _text(material.get("id"), "material.id")
        if material_id in materials:
            _fail("material.id", "duplicate identity")
        _text(material.get("name"), "material.name", empty=True)
        material["color"] = _vector(material.get("color"), "material.color", 4)
        material["emission"] = _vector(material.get("emission"), "material.emission", 3)
        if any(x < 0 for x in material["color"] + material["emission"]) or material["color"][3] > 1:
            _fail("material", "color/emission must be nonnegative and alpha at most 1")
        texture = _text(material.get("texturePath"), "material.texturePath", empty=True)
        material["_texture_path"] = _png_path(texture, base, "material.texturePath") if texture else ""
        materials[material_id] = material
    seen = set()
    for source in _array(payload.get("devices"), "devices", MAX_DEVICES):
        device = dict(_record(source, "device"))
        device_id = _text(device.get("id"), "device.id")
        if device_id in seen:
            _fail("device.id", "duplicate identity")
        seen.add(device_id)
        _text(device.get("name"), "device.name")
        for key in ("globalObjectId", "prefabGuid", "arcId", "slotId"):
            _text(device.get(key), f"device.{key}", empty=True)
        if "parentGlobalObjectId" in device:
            _text(device["parentGlobalObjectId"], "device.parentGlobalObjectId", empty=True)
        device["relativeMatrix"] = _matrix(device.get("relativeMatrix"), "device.relativeMatrix")
        parts = []
        for source_part in _array(device.get("parts"), "device.parts", MAX_PARTS_PER_DEVICE):
            part = dict(_record(source_part, "part"))
            _text(part.get("name"), "part.name", empty=True)
            if _text(part.get("meshId"), "part.meshId") not in meshes:
                _fail("part.meshId", "unknown mesh identity")
            part["relativeMatrix"] = _matrix(part.get("relativeMatrix"), "part.relativeMatrix")
            ids = _array(part.get("materialIds"), "part.materialIds", 128)
            if any(not isinstance(x, str) or x not in materials for x in ids):
                _fail("part.materialIds", "unknown material identity")
            parts.append(dict(part, materialIds=list(ids)))
            parts_total += 1
            if parts_total > MAX_PARTS_TOTAL:
                _fail("devices", "total part limit exceeded")
        device["parts"] = parts
        preview = device.get("screenPreview")
        if preview is not None:
            preview = dict(_record(preview, "screenPreview"))
            empty_preview = (preview.get("path") in (None, "") and
                             preview.get("relativeMatrix") in (None, []) and
                             type(preview.get("width", 0)) in (int, float) and preview.get("width", 0) == 0 and
                             type(preview.get("height", 0)) in (int, float) and preview.get("height", 0) == 0)
            if empty_preview:
                preview = None
            else:
                preview["relativeMatrix"] = _matrix(preview.get("relativeMatrix"), "screenPreview.relativeMatrix")
                for key in ("width", "height"):
                    preview[key] = _number(preview.get(key), f"screenPreview.{key}")
                    if preview[key] <= 0:
                        _fail(f"screenPreview.{key}", "must be positive")
                preview["_path"] = _png_path(preview.get("path"), base, "screenPreview.path")
        device["screenPreview"] = preview
        devices.append(device)
    paths = {value["_texture_path"] for value in materials.values() if value["_texture_path"]}
    paths.update(device["screenPreview"]["_path"] for device in devices if device["screenPreview"] is not None)
    image_bytes = image_pixels = 0
    for path in paths:
        image_bytes += Path(path).stat().st_size
        with Path(path).open("rb") as stream:
            width, height = struct.unpack(">II", stream.read(24)[16:24])
        image_pixels += width * height
    if image_bytes > MAX_IMAGES_TOTAL_BYTES or image_pixels > MAX_IMAGES_TOTAL_PIXELS:
        _fail("images", "total image memory limit exceeded")
    result.update(devices=devices, meshes=list(meshes.values()), materials=list(materials.values()))
    return result


def read_package(filepath):
    filepath = Path(filepath).resolve()
    if not filepath.is_file() or filepath.stat().st_size > MAX_FILE_BYTES:
        _fail("snapshot file", "missing or exceeds the 64 MiB limit")
    try:
        with filepath.open("r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        return validate_package(payload, filepath.parent)
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        raise LayoutValidationError(f"Cannot read snapshot: {exception}") from exception


def _multiply(left, right):
    return [sum(left[row * 4 + n] * right[n * 4 + column] for n in range(4))
            for row in range(4) for column in range(4)]


def _unit_scale(value):
    value = _number(value, "meters per Blender unit")
    if value <= 0:
        _fail("meters per Blender unit", "must be positive")
    return value


def unity_to_blender_matrix(row_major, meters_per_unit=1.0):
    """Inverse of rr_reference_layout.unity_reference_layout_matrix's FBX basis."""
    matrix = _matrix(row_major, "relativeMatrix")
    scale = _unit_scale(meters_per_unit)
    basis = [-scale, 0., 0., 0., 0., 0., scale, 0., 0., -scale, 0., 0., 0., 0., 0., 1.]
    inverse = [-1 / scale, 0., 0., 0., 0., 0., -1 / scale, 0., 0., 1 / scale, 0., 0., 0., 0., 0., 1.]
    return _multiply(_multiply(inverse, matrix), basis)


def unity_to_blender_vertices(flat_xyz, meters_per_unit=1.0):
    scale = _unit_scale(meters_per_unit)
    if len(flat_xyz) % 3:
        _fail("vertices", "expected flat XYZ values")
    return [value for n in range(0, len(flat_xyz), 3) for value in
            (-_number(flat_xyz[n], "vertex") / scale, -_number(flat_xyz[n + 2], "vertex") / scale,
             _number(flat_xyz[n + 1], "vertex") / scale)]


def unity_triangles_to_blender(indices):
    if len(indices) % 3:
        _fail("triangles", "expected triplets")
    return [value for n in range(0, len(indices), 3) for value in (indices[n], indices[n + 2], indices[n + 1])]


def _triples(values):
    return [tuple(values[index:index + 3]) for index in range(0, len(values), 3)]


def is_unity_layout_reference(obj):
    try:
        return bool(obj is not None and obj.get(REFERENCE_PROP, False))
    except (AttributeError, ReferenceError):
        return False


def _owned(value, reference_id=None):
    return is_unity_layout_reference(value) and value.get(OWNER_PROP) == OWNER and (
        reference_id is None or value.get(REFERENCE_ID_PROP) == reference_id)


def _tag(value, reference_id, token, device_id="", role="", part=-1):
    value[REFERENCE_PROP] = True
    value[OWNER_PROP] = OWNER
    value[REFERENCE_ID_PROP] = reference_id
    value[TOKEN_PROP] = token
    if device_id:
        value[DEVICE_ID_PROP] = device_id
        value[ROLE_PROP] = role
        value[PART_PROP] = part


def _collections(scene):
    pending, visited = [scene.collection], set()
    while pending:
        collection = pending.pop()
        if collection.as_pointer() in visited:
            continue
        visited.add(collection.as_pointer())
        yield collection
        pending.extend(collection.children)


def _find_collection(scene, reference_id):
    matches = [collection for collection in _collections(scene) if _owned(collection, reference_id)]
    if len(matches) > 1:
        _fail("managed collection", "multiple collections have this reference identity; resolve the duplicate first")
    return matches[0] if matches else None


def _as_matrix(values):
    return Matrix(tuple(tuple(values[row * 4:row * 4 + 4]) for row in range(4)))


def _parent_relative(obj, parent, matrix):
    obj.parent = parent
    # Parent inverse can retain shear from nonuniform Unity hierarchies, unlike
    # assigning matrix_basis (which Blender decomposes into local TRS).
    obj.matrix_parent_inverse = matrix
    obj.matrix_basis = Matrix.Identity(4)


def _parent_depth(obj):
    depth, visited = 0, set()
    while obj.parent is not None and obj.as_pointer() not in visited:
        visited.add(obj.as_pointer())
        obj = obj.parent
        depth += 1
    return depth


def _cleanup_resources(tokens):
    tokens = {token for token in tokens if isinstance(token, str) and token}
    for values in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for value in list(values):
            if _owned(value) and value.get(TOKEN_PROP) in tokens and value.users == 0:
                values.remove(value)


def _reference_module():
    try:
        from . import rr_reference_layout
        return rr_reference_layout
    except ImportError:
        try:
            from random_realm_builder_exporter import rr_reference_layout
            return rr_reference_layout
        except ImportError:
            try:
                import rr_reference_layout
                return rr_reference_layout
            except ImportError:
                return None


def _fallback_core(scene):
    module = _reference_module()
    return module.get_reference_object(scene) if module is not None else None


def import_package(payload, anchor, base_directory):
    """Stage a complete package, then update only the managed reference collection.

    Returns {collection, reference_id, device_count, mesh_count, snapshot_id, warnings}.
    Matching device roots retain their Blender Object identities on update. Artist
    children, unrelated collection members, shared datablocks and selection survive.
    """
    package = validate_package(payload, base_directory)
    if bpy is None:
        raise RuntimeError("import_package requires Blender")
    scene = bpy.context.scene
    if anchor is None or scene.objects.get(anchor.name) != anchor or is_unity_layout_reference(anchor):
        _fail("Core Object", "choose an existing building object in the current scene")
    _matrix([x for row in anchor.matrix_world for x in row], "Core Object transform")
    scale = 1.0 if scene.unit_settings.system == "NONE" else _unit_scale(scene.unit_settings.scale_length)
    reference_id = package["reference"]["id"]
    old = _find_collection(scene, reference_id)
    old_objects = [obj for obj in old.all_objects if _owned(obj, reference_id)] if old else []
    if old and (old.library or not old.is_editable):
        _fail("managed collection", "make this collection local before updating")
    if old and any(other != scene and old in list(_collections(other)) for other in bpy.data.scenes):
        _fail("managed collection", "collection is shared with another scene; make a separate reference collection first")
    roots = {}
    for obj in old_objects:
        if obj.library or not obj.is_editable:
            _fail("managed object", "make the previous reference objects local before updating")
        if obj.get(ROLE_PROP) == "ROOT":
            identity = obj.get(DEVICE_ID_PROP)
            if identity in roots:
                _fail("device identity", "duplicate previous device roots; resolve the duplicate first")
            roots[identity] = obj
    token = str(uuid.uuid4())
    stage = bpy.data.collections.new("Unity Layout (staging)")
    _tag(stage, reference_id, token)
    created = []
    reused_states = []
    unlinked = []
    artist_children = [(child, obj, child.matrix_parent_inverse.copy(), child.matrix_basis.copy(), obj.matrix_world.copy())
                       for obj in old_objects for child in obj.children if not _owned(child, reference_id)]
    artist_touched = []
    target = old or stage
    warnings = []
    view_layer = bpy.context.view_layer
    selected = [obj for obj in view_layer.objects if obj.select_get(view_layer=view_layer)]
    active = bpy.context.view_layer.objects.active
    selection_keys = {(obj.get(DEVICE_ID_PROP), obj.get(ROLE_PROP), obj.get(PART_PROP)): obj.hide_select
                      for obj in selected if _owned(obj, reference_id)}
    active_key = (active.get(DEVICE_ID_PROP), active.get(ROLE_PROP), active.get(PART_PROP)) if _owned(active, reference_id) else None
    new_objects = {}
    images = {}
    metadata = {"rr_unity_layout_snapshot_id": package["snapshotId"], "rr_unity_layout_scene_path": package["scenePath"],
                "rr_unity_layout_source_stable_id": package["reference"]["sourceStableId"],
                "rr_unity_layout_source_blend": package["reference"]["sourceBlend"],
                "rr_unity_layout_authoring_frame_in_root": package["reference"]["authoringFrameInRoot"],
                "rr_unity_layout_reference_world_matrix": package["reference"]["worldMatrix"]}
    previous_metadata = {key: (key in target, target.get(key)) for key in metadata}
    for key, (present, value) in list(previous_metadata.items()):
        if present and hasattr(value, "to_list"):
            previous_metadata[key] = (present, value.to_list())

    def image_for(path):
        if path not in images:
            image = bpy.data.images.load(path, check_existing=False)
            _tag(image, reference_id, token)
            if any(dimension <= 0 for dimension in image.size):
                _fail("image", "PNG could not be decoded: " + path)
            image.pack()
            images[path] = image
        return images[path]

    def material_for(name, color, emission, path=""):
        material = bpy.data.materials.new(name or "Unity Layout Material")
        _tag(material, reference_id, token)
        material.diffuse_color = tuple(color)
        material.use_nodes = True
        material.use_backface_culling = False
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = tuple(color)
        shader.inputs["Alpha"].default_value = color[3]
        emission_input = shader.inputs.get("Emission Color") or shader.inputs.get("Emission")
        if emission_input is not None:
            emission_input.default_value = tuple(emission) + (1.,)
        strength = shader.inputs.get("Emission Strength")
        if strength is not None:
            strength.default_value = 1.0
        if path:
            texture = material.node_tree.nodes.new("ShaderNodeTexImage")
            texture.image = image_for(path)
            material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
            material.node_tree.links.new(texture.outputs["Alpha"], shader.inputs["Alpha"])
        return material

    def object_for(name, data, device_id, role, part, parent, relative):
        obj = bpy.data.objects.new(name, data)
        _tag(obj, reference_id, token, device_id, role, part)
        created.append(obj)
        stage.objects.link(obj)
        _parent_relative(obj, parent, _as_matrix(unity_to_blender_matrix(relative, scale)))
        obj.hide_select = True
        new_objects[(device_id, role, part)] = obj
        return obj

    try:
        materials = {value["id"]: material_for(value["name"], value["color"], value["emission"], value["_texture_path"])
                     for value in package["materials"]}
        fallback = material_for("Unity Layout Default", [0.65, 0.65, 0.65, 1.], [0., 0., 0.])
        slots = {value["id"]: len(value["submeshes"]) for value in package["meshes"]}
        mesh_names = {}
        for device in package["devices"]:
            for part in device["parts"]:
                slots[part["meshId"]] = max(slots[part["meshId"]], len(part["materialIds"]))
                mesh_names.setdefault(part["meshId"], part["name"] or device["name"])
        meshes = {}
        for mesh_index, value in enumerate(package["meshes"], 1):
            label = mesh_names.get(value["id"]) or ("Mesh " + str(mesh_index).zfill(2))
            mesh = bpy.data.meshes.new("Unity Layout · " + label)
            _tag(mesh, reference_id, token)
            faces, assignments = [], []
            for index, submesh in enumerate(value["submeshes"]):
                triangles = _triples(unity_triangles_to_blender(submesh["triangles"]))
                faces.extend(triangles)
                assignments.extend([index] * len(triangles))
            mesh.from_pydata(_triples(unity_to_blender_vertices(value["vertices"], scale)), [], faces)
            for _ in range(slots[value["id"]]):
                mesh.materials.append(fallback)
            for polygon, assignment in zip(mesh.polygons, assignments):
                polygon.material_index = assignment
            if value["uv"]:
                uv = mesh.uv_layers.new(name="UVMap")
                for loop in mesh.loops:
                    index = loop.vertex_index * 2
                    uv.data[loop.index].uv = value["uv"][index:index + 2]
            mesh.update()
            meshes[value["id"]] = mesh
        for device in package["devices"]:
            identity = device["id"]
            root = object_for(device["name"], None, identity, "ROOT", -1, anchor, device["relativeMatrix"])
            root.empty_display_type = "PLAIN_AXES"
            root.empty_display_size = 0.25 / scale
            root.show_name = not any(meshes[part["meshId"]].polygons for part in device["parts"]) and device["screenPreview"] is None
            for index, part in enumerate(device["parts"]):
                obj = object_for(part["name"] or device["name"], meshes[part["meshId"]], identity, "PART", index,
                                 root, part["relativeMatrix"])
                for slot in obj.material_slots:
                    slot.link = "OBJECT"
                    slot.material = fallback
                for slot, material_id in zip(obj.material_slots, part["materialIds"]):
                    slot.material = materials[material_id]
            preview = device["screenPreview"]
            if preview is not None:
                width, height = preview["width"] * 0.5, preview["height"] * 0.5
                mesh = bpy.data.meshes.new("Unity Layout Screen")
                _tag(mesh, reference_id, token)
                vertices = [-width, -height, 0., width, -height, 0., width, height, 0., -width, height, 0.]
                # Unity UI's readable face is -Z. Reflect its winding with the
                # vertices, just like ordinary mesh parts, so it remains front-facing.
                faces = _triples(unity_triangles_to_blender([0, 2, 1, 0, 3, 2]))
                mesh.from_pydata(_triples(unity_to_blender_vertices(vertices, scale)), [], faces)
                uv = mesh.uv_layers.new(name="UVMap")
                corners = [(0., 0.), (1., 0.), (1., 1.), (0., 1.)]
                for loop in mesh.loops:
                    uv.data[loop.index].uv = corners[loop.vertex_index]
                material = material_for("Unity Layout Screen", [1., 1., 1., 1.], [0.15, 0.15, 0.15], preview["_path"])
                shader = material.node_tree.nodes.get("Principled BSDF")
                emission_input = shader.inputs.get("Emission Color") or shader.inputs.get("Emission")
                if emission_input is not None:
                    texture = next(node for node in material.node_tree.nodes if node.type == "TEX_IMAGE")
                    material.node_tree.links.new(texture.outputs["Color"], emission_input)
                    if shader.inputs.get("Emission Strength") is not None:
                        shader.inputs["Emission Strength"].default_value = 0.3
                mesh.materials.append(material)
                object_for(device["name"] + " Screen Preview", mesh, identity, "SCREEN", -1, root, preview["relativeMatrix"])

        # Staging has completed. Keep existing roots so artist children/constraints
        # and the selected device's identity continue to point at the same Object.
        keep = set()
        for device in package["devices"]:
            identity = device["id"]
            previous = roots.get(identity)
            staged_root = new_objects[(identity, "ROOT", -1)]
            if previous is None:
                continue
            reused_states.append((previous, previous.parent, previous.matrix_parent_inverse.copy(),
                                  previous.matrix_basis.copy(), previous.show_name))
            _parent_relative(previous, anchor, staged_root.matrix_parent_inverse.copy())
            previous.show_name = staged_root.show_name
            for child in list(staged_root.children):
                # Blender's parent setter clears the inverse. Preserve the staged
                # PART/SCREEN relative frame while reusing the existing device root.
                _parent_relative(child, previous, child.matrix_parent_inverse.copy())
            new_objects[(identity, "ROOT", -1)] = previous
            keep.add(previous)
        for obj in created:
            if obj.get(ROLE_PROP) == "ROOT" and obj.get(DEVICE_ID_PROP) in roots:
                continue
            target.objects.link(obj) if target != stage else None
        obsolete = [obj for obj in old_objects if obj not in keep]
        bpy.context.view_layer.update()
        for child, parent, inverse, basis, parent_world in artist_children:
            next_parent = new_objects.get((parent.get(DEVICE_ID_PROP), parent.get(ROLE_PROP), parent.get(PART_PROP)), anchor) if parent in obsolete else parent
            artist_touched.append((child, parent, inverse, basis))
            child.parent = next_parent
            child.matrix_parent_inverse = next_parent.matrix_world.inverted() @ parent_world @ inverse
            child.matrix_basis = basis
        for obj in obsolete:
            if old is not None and old.objects.get(obj.name) == obj:
                old.objects.unlink(obj)
                unlinked.append(obj)
        if old is None:
            stage.name = "Unity Layout - " + package["reference"]["name"]
            scene.collection.children.link(stage)
        for key, value in metadata.items():
            target[key] = value
        bpy.context.view_layer.update()
    except Exception:
        # No previous owned object/data is destroyed before commit completes.
        for child, parent, inverse, basis in reversed(artist_touched):
            child.parent, child.matrix_parent_inverse, child.matrix_basis = parent, inverse, basis
        for obj in unlinked:
            old.objects.link(obj)
        for obj, parent, inverse, basis, show_name in reversed(reused_states):
            obj.parent, obj.matrix_parent_inverse, obj.matrix_basis = parent, inverse, basis
            obj.show_name = show_name
        for key, (present, value) in previous_metadata.items():
            if present:
                target[key] = value
            elif key in target:
                del target[key]
        for obj in reversed(created):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(stage)
        _cleanup_resources({token})
        raise

    # Cleanup is limited to owned, unshared data. A cleanup failure leaves an
    # orphan reference instead of failing after replacing the visible layout.
    old_tokens = {obj.get(TOKEN_PROP) for obj in old_objects}
    try:
        for obj in reversed(created):
            if obj.get(ROLE_PROP) == "ROOT" and obj.get(DEVICE_ID_PROP) in roots:
                bpy.data.objects.remove(obj, do_unlink=True)
        if target != stage:
            bpy.data.collections.remove(stage)
        for obj in sorted(obsolete, key=_parent_depth, reverse=True):
            if not obj.users_collection and obj.users == 0:
                bpy.data.objects.remove(obj)
        _cleanup_resources(old_tokens | {token})
    except (RuntimeError, ReferenceError) as exception:
        warnings.append("Layout imported; unused owned reference data was retained: " + str(exception))
    for key in selection_keys:
        obj = new_objects.get(key)
        if obj is not None and bpy.context.view_layer.objects.get(obj.name) == obj:
            obj.hide_select = selection_keys[key]
            obj.select_set(True, view_layer=view_layer)
    if active_key is not None:
        bpy.context.view_layer.objects.active = new_objects.get(active_key)
    elif active is not None and bpy.context.view_layer.objects.get(active.name) == active:
        bpy.context.view_layer.objects.active = active
    return {"collection": target, "reference_id": reference_id, "device_count": len(package["devices"]),
            "mesh_count": len(package["meshes"]), "snapshot_id": package["snapshotId"], "warnings": warnings}


if bpy is not None:
    def _visibility_changed(settings, _context):
        collection = settings.managed_collection
        if collection is not None and _owned(collection) and not collection.library and collection.is_editable:
            collection.hide_viewport = settings.hidden
            collection.hide_render = settings.hidden

    class RRUnityDeviceLayoutSettings(bpy.types.PropertyGroup):
        snapshot_path: bpy.props.StringProperty(name="Snapshot", subtype="FILE_PATH")
        core_object: bpy.props.PointerProperty(name="Core Object", type=bpy.types.Object,
                                               poll=lambda _self, obj: not is_unity_layout_reference(obj))
        managed_collection: bpy.props.PointerProperty(type=bpy.types.Collection)
        hidden: bpy.props.BoolProperty(name="Hide References", update=_visibility_changed)
        status: bpy.props.StringProperty(default="Choose a snapshot and Core Object.")

    class RR_OT_import_unity_device_layout(bpy.types.Operator):
        bl_idname = "rr_builder.import_unity_device_layout"
        bl_label = "Import / Update"
        bl_description = "Import a Unity device snapshot as reference-only geometry; update its managed collection"
        bl_options = {"REGISTER", "UNDO"}

        @classmethod
        def poll(cls, context):
            return context.mode == "OBJECT"

        def execute(self, context):
            settings = context.scene.rr_unity_device_layout
            try:
                path = Path(bpy.path.abspath(settings.snapshot_path)).resolve()
                payload = read_package(path)
                anchor = settings.core_object or _fallback_core(context.scene)
                result = import_package(payload, anchor, path.parent)
                settings.managed_collection = result["collection"]
                render_hidden = result["collection"].hide_render
                settings.hidden = result["collection"].hide_viewport
                result["collection"].hide_render = render_hidden
                settings.status = f"Updated {result['device_count']} devices. References stay out of export."
                self.report({"WARNING"} if result["warnings"] else {"INFO"},
                            "; ".join(result["warnings"]) if result["warnings"] else settings.status)
                return {"FINISHED"}
            except (ValueError, RuntimeError, OSError) as exception:
                settings.status = "Import failed: " + str(exception)
                self.report({"ERROR"}, settings.status)
                return {"CANCELLED"}

    class RR_PT_unity_device_layout(bpy.types.Panel):
        bl_label = "Unity Layout"
        bl_idname = "RR_PT_unity_device_layout"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "RandomRealm"
        bl_parent_id = "RR_PT_builder_exporter"
        bl_options = {"DEFAULT_CLOSED"}

        def draw(self, context):
            settings = context.scene.rr_unity_device_layout
            layout = self.layout
            layout.prop(settings, "snapshot_path")
            layout.prop(settings, "core_object")
            if settings.core_object is None:
                fallback = _fallback_core(context.scene)
                layout.label(text="Core: " + (fallback.name if fallback else "choose a building"))
            layout.operator("rr_builder.import_unity_device_layout", icon="IMPORT")
            row = layout.row()
            row.enabled = (settings.managed_collection is not None and _owned(settings.managed_collection)
                           and not settings.managed_collection.library and settings.managed_collection.is_editable)
            row.prop(settings, "hidden")
            if settings.managed_collection is not None:
                layout.label(text=settings.managed_collection.name, icon="OUTLINER_COLLECTION")
            for line in textwrap.wrap(settings.status, width=42):
                layout.label(text=line)

    CLASSES = (RRUnityDeviceLayoutSettings, RR_OT_import_unity_device_layout, RR_PT_unity_device_layout)
else:
    CLASSES = ()


def register():
    if bpy is None:
        return
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
        bpy.types.Scene.rr_unity_device_layout = bpy.props.PointerProperty(type=RRUnityDeviceLayoutSettings)
    except Exception:
        unregister()
        raise


def unregister():
    if bpy is None:
        return
    if hasattr(bpy.types.Scene, "rr_unity_device_layout"):
        del bpy.types.Scene.rr_unity_device_layout
    for cls in reversed(CLASSES):
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)

