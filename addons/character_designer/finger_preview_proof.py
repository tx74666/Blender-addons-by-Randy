"""Fast complete-data equality proof for repeated read-only joint previews.

The persistent legacy fingerprint is unchanged. Cold/changed data still goes
through that validator; warm equality compares packed RNA buffers instead of
constructing large decimal strings and serializing every vertex group twice.
No proof is used by modeling/weight write operations.
"""
from array import array
import hashlib
import pickle

import bpy
import bmesh

from .mesh_mirror import INTERNAL_ATTRIBUTES, _value


def signature(obj):
    """Capture all data used by the legacy fingerprint, with bulk RNA reads."""
    if obj.mode == 'EDIT': obj.update_from_editmode()
    # Edit Mode attribute RNA buffers require a synchronized unlinked snapshot.
    mesh = obj.data.copy() if obj.mode == 'EDIT' else obj.data
    owned = mesh != obj.data
    digest = hashlib.blake2b(digest_size=32)

    def meta(value):
        data = repr(value).encode()
        digest.update(len(data).to_bytes(8, 'little')); digest.update(data)

    def buffer(items, field, width=1, floating=False, boolean=False):
        if len(items) and not hasattr(items[0], field):
            if field != 'use_freestyle_mark': raise AttributeError(field)
            meta((field, len(items), 'absent')); return
        # Matching RNA's float32/int32/boolean buffer type enables bulk copy.
        # Packed short vectors use exact int32 expansion, never float rounding.
        values = array('b' if boolean else 'f' if floating else 'i', [0]) * (len(items)*width)
        if values: items.foreach_get(field, values)
        meta((field, len(items), width, floating)); digest.update(values)

    try:
        meta((tuple(tuple(row) for row in obj.matrix_world), obj.mode, obj.active_shape_key_index))
        buffer(mesh.vertices, 'co', 3, True)
        buffer(mesh.vertices, 'hide', boolean=True)
        buffer(mesh.edges, 'vertices', 2)
        buffer(mesh.loops, 'vertex_index')
        for field in ('loop_start', 'loop_total', 'material_index', 'use_smooth', 'use_freestyle_mark'):
            buffer(mesh.polygons, field, boolean=field.startswith('use_'))
        for field in ('use_seam', 'use_edge_sharp', 'use_freestyle_mark'):
            buffer(mesh.edges, field, boolean=True)
        buffer(mesh.edges, 'hide', boolean=True); buffer(mesh.polygons, 'hide', boolean=True)
        meta(tuple(layer.name for layer in mesh.uv_layers))
        for layer in mesh.uv_layers:
            if hasattr(layer, 'uv'): buffer(layer.uv, 'vector', 2, True)
            else: buffer(layer.data, 'uv', 2, True)
        shapes = mesh.shape_keys
        meta(None if shapes is None else (shapes.use_relative, shapes.eval_time, len(shapes.key_blocks)))
        if shapes:
            for key in shapes.key_blocks:
                meta((key.name, key.value, key.mute, key.slider_min, key.slider_max,
                      key.vertex_group, key.relative_key.name if key.relative_key else None))
                buffer(getattr(key, 'points', key.data), 'co', 3, True)
        for attr in mesh.attributes:
            if attr.name in INTERNAL_ATTRIBUTES or attr.name.startswith(('.select', '.hide')): continue
            meta((attr.name, attr.domain, attr.data_type, len(attr.data)))
            if not attr.data: continue
            field, value = _value(attr.data[0])
            if attr.data_type in {'FLOAT', 'FLOAT_VECTOR', 'FLOAT2', 'FLOAT_COLOR', 'BYTE_COLOR',
                                  'INT', 'INT8', 'INT16_2D', 'INT32_2D', 'BOOLEAN'}:
                buffer(attr.data, field, len(value) if isinstance(value, tuple) else 1,
                       attr.data_type not in {'INT', 'INT8', 'INT16_2D', 'INT32_2D', 'BOOLEAN'},
                       boolean=attr.data_type == 'BOOLEAN')
            else: meta(tuple(_value(item) for item in attr.data))
        meta(mesh.has_custom_normals)
        if mesh.has_custom_normals: buffer(mesh.corner_normals, 'vector', 3, True)
        meta(tuple(m.name_full if m else None for m in mesh.materials))
        meta(tuple((g.name, g.index, bool(g.lock_weight)) for g in obj.vertex_groups))
        group_count = len(obj.vertex_groups)
        rows = []
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            deform = bm.verts.layers.deform.active
            if deform:
                for i, vertex in enumerate(bm.verts):
                    values = tuple(sorted((g, w) for g, w in vertex[deform].items() if 0 <= g < group_count))
                    if values: rows.append((i, values))
        else:
            for vertex in mesh.vertices:
                values = tuple(sorted((m.group, m.weight) for m in vertex.groups if 0 <= m.group < group_count))
                if values: rows.append((vertex.index, values))
        # Binary encoding only; never unpickle external data. One C serialization
        # avoids tens of thousands of Python array append calls on weighted rigs.
        digest.update(pickle.dumps(rows, protocol=5))
        return digest.digest()
    finally:
        if owned: bpy.data.meshes.remove(mesh)


def verify(obj, state, previous=None):
    """Reuse only an exact equality proof for the same source and signature."""
    from . import finger_workflow as workflow
    key = (obj.as_pointer(), obj.data.as_pointer(), state.source.as_pointer() if state.source else 0,
           state.signature)
    try: current = signature(obj)
    except (AttributeError, TypeError, OverflowError, RuntimeError):
        # An unfamiliar RNA buffer may use the old path, never a weaker proof.
        workflow.check_source(obj, state)
        return None
    proof = (key, current)
    if previous != proof: workflow.check_source(obj, state)
    return proof
