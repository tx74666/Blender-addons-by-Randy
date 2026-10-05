"""Create an independent opaque PBR material from persisted manual bake maps.

Validation proves image structure and persistence, not bake appearance or UV
quality. Preview the resulting material before assigning it to user objects.
Source materials, shared images, and object material slots are never changed.
"""

import json
import os
import struct
import zlib

import bpy


_ROLES = ("BaseColor", "Roughness", "Metallic", "Normal")
_COLORSPACES = {role: "sRGB" if role == "BaseColor" else "Non-Color" for role in _ROLES}
_NAME_BYTES = 63


class _PackedReader:
    def __init__(self, data):
        self.data = memoryview(data)
        self.position = 0

    def read(self, count):
        result = self.data[self.position:self.position + count]
        self.position += len(result)
        return result


def _png_dimensions(reader):
    """Check PNG chunks/CRCs without decoding or copying the full pixel buffer."""
    if reader.read(8) != b"\x89PNG\r\n\x1a\n":
        raise ValueError("The saved image is not a valid PNG.")
    dimensions = None
    has_data = False
    while True:
        header = reader.read(8)
        if len(header) != 8:
            raise ValueError("The saved PNG is incomplete.")
        size, kind = struct.unpack(">I4s", header)
        if dimensions is None and (kind != b"IHDR" or size != 13):
            raise ValueError("The saved PNG has no valid image header.")
        checksum = zlib.crc32(kind)
        remaining = size
        while remaining:
            data = reader.read(min(remaining, 1024 * 1024))
            if not data:
                raise ValueError("The saved PNG image data is incomplete.")
            if kind == b"IHDR" and remaining == size:
                dimensions = struct.unpack(">II", data[:8])
            checksum = zlib.crc32(data, checksum)
            remaining -= len(data)
        stored_crc = reader.read(4)
        if len(stored_crc) != 4 or struct.unpack(">I", stored_crc)[0] != checksum & 0xffffffff:
            raise ValueError("The saved PNG image data failed its checksum.")
        if kind == b"IDAT" and size:
            has_data = True
        if kind == b"IEND":
            if size or not has_data or not dimensions or min(dimensions) <= 0:
                raise ValueError("The saved PNG has no valid image data.")
            return dimensions


def validate_baked_images(images_by_role):
    """Return role -> image after read-only validation; BaseColor is required.

    Values may be Images or ``(image, saved_path)`` pairs. Maps must already
    have the correct color space and be persisted PNGs, either saved or packed.
    A dirty flag and allocated blank pixels alone do not prove a finished bake.
    """
    if not isinstance(images_by_role, dict) or "BaseColor" not in images_by_role:
        raise ValueError("A saved Base Color map is required to create a baked material.")
    if any(role not in _ROLES for role in images_by_role):
        raise ValueError("Choose only BaseColor, Roughness, Metallic, and Normal maps.")
    normalized = {}
    for role, value in images_by_role.items():
        supplied_path = ""
        if isinstance(value, (tuple, list)):
            if len(value) != 2:
                raise ValueError(f"{role}: expected an image and its saved path.")
            value, supplied_path = value
        image = value
        if not isinstance(image, bpy.types.Image) or bpy.data.images.get(image.name) != image:
            raise ValueError(f"{role}: the image is missing.")
        dimensions = tuple(int(size) for size in image.size[:2])
        if len(dimensions) != 2 or min(dimensions) <= 0 or image.source not in {"FILE", "GENERATED"}:
            raise ValueError(f"{role}: choose a valid single-image bake result.")
        if image.colorspace_settings.name != _COLORSPACES[role]:
            raise ValueError(f"{role}: the image must already use {_COLORSPACES[role]}; shared images were not changed.")
        if image.packed_file is not None:
            encoded_dimensions = _png_dimensions(_PackedReader(image.packed_file.data))
        else:
            image_path = bpy.path.abspath(image.filepath_raw or image.filepath,
                                          library=getattr(image, "library", None))
            if supplied_path and os.path.normcase(os.path.abspath(bpy.path.abspath(str(supplied_path)))) != os.path.normcase(os.path.abspath(image_path)):
                raise ValueError(f"{role}: the supplied saved path differs from the image path.")
            if not image_path or not os.path.isfile(image_path):
                raise ValueError(f"{role}: save or pack the bake image before creating a material.")
            with open(image_path, "rb") as handle:
                encoded_dimensions = _png_dimensions(handle)
        if encoded_dimensions != dimensions:
            raise ValueError(f"{role}: saved and in-memory image dimensions differ; save the result again.")
        normalized[role] = image
    return normalized


def _display_name(source_name):
    sequence = 0
    while True:
        suffix = "_baked" if not sequence else f"_baked_{sequence:02d}"
        prefix = str(source_name or "Material").encode("utf-8")[:_NAME_BYTES - len(suffix.encode("utf-8"))]
        name = prefix.decode("utf-8", errors="ignore") + suffix
        if bpy.data.materials.get(name) is None:
            return name
        sequence += 1


def _populate_material(material, images, uv_map_name):
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "Principled BSDF"
    principled.location = (300, 0)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Material Output"
    output.location = (640, 0)
    output.is_active_output = True
    tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    uv_node = None
    if uv_map_name:
        uv_node = tree.nodes.new("ShaderNodeUVMap")
        uv_node.name = "Bake UV Map"
        uv_node.uv_map = uv_map_name
        uv_node.location = (-650, 0)
    for index, role in enumerate(_ROLES):
        if role not in images:
            continue
        node = tree.nodes.new("ShaderNodeTexImage")
        node.name = "Base Color" if role == "BaseColor" else role
        node.label = node.name
        node.image = images[role]
        node.location = (-350, -index * 220)
        if uv_node is not None:
            tree.links.new(uv_node.outputs["UV"], node.inputs["Vector"])
        if role == "Normal":
            normal = tree.nodes.new("ShaderNodeNormalMap")
            normal.name = "Normal Map"
            normal.location = (0, -index * 220)
            normal.space = "TANGENT"
            normal.uv_map = uv_map_name
            for attribute, setting in (("convention", "OPENGL"), ("base", "DISPLACED")):
                if hasattr(normal, attribute):
                    setattr(normal, attribute, setting)
            tree.links.new(node.outputs["Color"], normal.inputs["Color"])
            tree.links.new(normal.outputs["Normal"], principled.inputs["Normal"])
        else:
            socket = "Base Color" if role == "BaseColor" else role
            tree.links.new(node.outputs["Color"], principled.inputs[socket])


def create_baked_material(source, images_by_role, *, uv_map_name=""):
    """Create only a new clean opaque material; never assign it to any object.

    Images are validated before creation and are shared without modification.
    If construction fails, only the newly created material is removed.
    """
    if not isinstance(source, bpy.types.Material) or bpy.data.materials.get(source.name) != source:
        raise ValueError("Choose an existing source material.")
    if not isinstance(uv_map_name, str):
        raise ValueError("The bake UV map name must be text.")
    images = validate_baked_images(images_by_role)
    name = _display_name(source.name)
    material = None
    try:
        material = bpy.data.materials.new(name)
        if material.name != name:
            raise ValueError("The baked material name changed during creation; try again.")
        _populate_material(material, images, uv_map_name)
        material.use_fake_user = True
        material["rr_pbr_baked_source_name"] = source.name
        material["rr_pbr_baked_roles"] = json.dumps([role for role in _ROLES if role in images])
        material["rr_pbr_baked_uv_map"] = uv_map_name
        material["rr_pbr_baked_quality"] = "preview_required"
        return material
    except Exception:
        if material is not None:
            bpy.data.materials.remove(material)
        raise
