"""Portable forearm calibration and subdivision correspondence for Unity.

Capture reads the artist's mesh. Baking operates only on the export worker's
temporary evaluation object. No source pose, topology, weights or keys change.
"""
from array import array
import hashlib
import math

SCHEMA = "cdesigner.forearm/1"
UV_NAME = "CD_Forearm_Vertex_ID"


def vector(value):
    return dict(zip(("x", "y", "z"), map(float, value)))


def capture(obj):
    from . import forearm_twist as twist
    from . import forearm_twist_profile as profile
    from .forearm_twist_math import profile_ratio, _rotation
    from .unity_export_worker import _shape_inputs

    records = twist._records(obj)
    if not records:
        return None
    rig = twist._check_mesh(obj)
    topology = twist._topology(obj.data)
    keys = obj.data.shape_keys
    owned = []
    for side, record in records.items():
        key = twist._managed_key(obj, side, record, repair_name=False)
        if record["topology"] != topology or record["armature"] != rig.name:
            raise ValueError(f"{obj.name}: recapture forearm loops after the topology/binding change.")
        if record["rest"] != twist._rest_signature(rig, record["chain"]):
            raise ValueError(f"{obj.name}: forearm calibration rest bones have changed.")
        if key.relative_key != keys.reference_key or key.vertex_group:
            raise ValueError(f"{obj.name}: the managed forearm key has changed.")
        indices, positions = record.get("vertices"), record.get("positions")
        if (not isinstance(indices, list) or not isinstance(positions, list)
                or len(indices) != len(positions)
                or any(isinstance(index, bool) or not isinstance(index, int)
                       or not 0 <= index < len(obj.data.vertices) for index in indices)
                or len(set(indices)) != len(indices)
                or any(isinstance(position, bool) or not isinstance(position, (int, float))
                       or not math.isfinite(position) for position in positions)):
            raise ValueError(f"{obj.name}: saved forearm vertex correspondence is invalid.")
        owned.append(key.name)
    # These exports deliberately support the existing Armature-first prototype.
    # Geometry-dependent modifiers are not a constant subdivision map.
    for modifier in list(obj.modifiers)[1:]:
        if modifier.show_viewport and modifier.type != 'SUBSURF':
            raise ValueError(f"{obj.name}: Unity forearm correction currently supports only Subdivision after Armature; found {modifier.type}.")
    mesh_from_arm = obj.matrix_world.inverted() @ rig.matrix_world
    # Axis-angle rotation can be transported through a positive uniform scale,
    # but reflection changes its handedness and shear is not a rigid rotation.
    try:
        _rotation(mesh_from_arm)
    except ValueError as error:
        raise ValueError(f"{obj.name}: unsupported mesh-to-rig transform: {error}") from error
    basis, artists = _shape_inputs(obj, owned)
    source_indices = sorted({i for record in records.values() for i in record["vertices"]})
    weights = twist._weights(obj, rig, source_indices)
    result = {"objectName": obj.name, "sourceVertexCount": len(obj.data.vertices),
              "sourceTopology": topology, "sides": [], "sources": [], "shapes": []}
    used = set()
    for side, record in sorted(records.items()):
        lower, hand = record["chain"][1:]
        axis = rig.data.bones[lower].tail_local - rig.data.bones[lower].head_local
        axis = (mesh_from_arm.to_3x3() @ axis).normalized()
        pivot = mesh_from_arm @ rig.data.bones[hand].head_local
        side_index = len(result["sides"])
        result["sides"].append({"name": side, "lowerBone": lower, "handBone": hand,
                                "axis": vector(axis), "pivot": vector(pivot),
                                "enabled": bool(record.get("enabled", True))})
        knots = list(profile.profile_knots(record["rings"]))
        bounded = "range_start" in record
        if bounded:
            first, last = profile.record_range(record)
        else:
            knots = [(0., 0.)] + [(p, r) for p, r in knots if 1e-6 < p < 1.-1e-6] + [(1., 1.)]
        for index, position in zip(record["vertices"], record["positions"]):
            influence = profile.range_influence(position, record["rings"], first, last,
                                                  record.get("transition", .1)) if bounded else 1.
            if influence <= 0.:
                continue
            if index in used:
                raise ValueError(f"{obj.name}: left/right forearm ranges overlap.")
            used.add(index)
            w = weights[index]
            result["sources"].append({"vertex": index, "side": side_index,
                "point": vector(basis[3*index:3*index+3]), "ratio": profile_ratio(position, knots),
                "influence": influence, "lowerWeight": w.get(lower, 0.), "handWeight": w.get(hand, 0.)})
    for shape in artists:
        offsets = []
        for sample in result["sources"]:
            index = sample["vertex"] * 3
            offsets.append(vector([shape["coordinates"][index+j] - basis[index+j] for j in range(3)]))
        # Keep only keys which can affect this correction's input.
        if any(abs(component) > 1e-12 for offset in offsets for component in offset.values()):
            result["shapes"].append({"name": shape["name"], "deltas": offsets})
    if not result["sources"]:
        raise ValueError(f"{obj.name}: the saved forearm range has no affected vertices.")
    return result


def bake(payload, temporary, evaluate, basis, base_mesh):
    """Measure the linear Subdivision response, three source impulses per pass."""
    from mathutils import Vector
    count = len(base_mesh.vertices)
    base = array('f', [0.]) * (3 * count)
    base_mesh.vertices.foreach_get('co', base)
    expected_topology = _topology(base_mesh)
    entries = [[] for _ in range(count)]
    samples = payload["sources"]
    # Geometry at zero avoids subtracting two large coordinates. Fixed Subdivision
    # is linear, including saved crease weights: its scalar response is the
    # same in x, y and z. No vertex-group interpolation is used for this map.
    for start in range(0, len(samples), 3):
        coordinates = array('f', [0.]) * len(basis)
        batch = samples[start:start+3]
        for channel, sample in enumerate(batch):
            coordinates[3 * sample["vertex"] + channel] = 1.
        mesh = evaluate(coordinates)
        try:
            if _topology(mesh) != expected_topology:
                raise ValueError("Subdivision vertex correspondence changed during forearm export.")
            current = array('f', [0.]) * len(base)
            mesh.vertices.foreach_get('co', current)
            for dest in range(count):
                for channel in range(len(batch)):
                    weight = current[3*dest+channel]
                    if abs(weight) > 1e-9:
                        entries[dest].append((start+channel, float(weight)))
        finally:
            import bpy
            bpy.data.meshes.remove(mesh)
    # A deterministic non-axis displacement catches wrong indexing or nonlinear
    # modifier behavior before anything is published.
    test = array('f', basis)
    displacements = []
    for index, sample in enumerate(samples):
        delta = Vector((math.sin(index*1.7)*.013, math.cos(index*.9)*.011, math.sin(index*.3)*.017))
        displacements.append(delta)
        for channel in range(3):
            test[3*sample["vertex"]+channel] += delta[channel]
    mesh = evaluate(test)
    try:
        if _topology(mesh) != expected_topology:
            raise ValueError("Subdivision vertex correspondence changed during forearm export.")
        maximum = 0.
        for index, vertex in enumerate(mesh.vertices):
            predicted = Vector(base[3*index:3*index+3])
            for source, weight in entries[index]:
                predicted += displacements[source] * weight
            maximum = max(maximum, (vertex.co-predicted).length)
        if maximum > 3e-6:
            raise ValueError(f"Forearm subdivision correspondence failed validation ({maximum:.6g}).")
    finally:
        import bpy
        bpy.data.meshes.remove(mesh)
    # Add an unused UV channel to the disposable exported mesh only. Unity can
    # reorder/split vertices at UV/normal seams; IDs preserve exact association.
    if UV_NAME in base_mesh.uv_layers or len(base_mesh.uv_layers) >= 8:
        raise ValueError("Forearm export needs one unused UV channel for stable vertex IDs.")
    channel = len(base_mesh.uv_layers)
    active_index = base_mesh.uv_layers.active_index
    render_layer = next((layer for layer in base_mesh.uv_layers if layer.active_render), None)
    uv = base_mesh.uv_layers.new(name=UV_NAME)
    for loop in base_mesh.loops:
        uv.data[loop.index].uv = (float(loop.vertex_index+1), .375)
    if channel:
        base_mesh.uv_layers.active_index = active_index
        uv.active_render = False
        if render_layer is not None:
            render_layer.active_render = True
    return {**payload, "vertexCount": count, "idUvChannel": channel,
            "positions": [vector(v.co) for v in base_mesh.vertices],
            "stencils": [{"vertex": dest, "sources": [i for i, _ in entry],
                           "weights": [w for _, w in entry]}
                          for dest, entry in enumerate(entries) if entry],
            "stencilCheckMaxError": maximum}


def _topology(mesh):
    """A constant vertex count alone cannot establish correspondence."""
    digest = hashlib.sha256()
    for collection, name, width in ((mesh.edges, 'vertices', 2),
                                    (mesh.loops, 'vertex_index', 1),
                                    (mesh.polygons, 'loop_total', 1)):
        values = array('i', [0]) * (width * len(collection))
        collection.foreach_get(name, values)
        digest.update(values.tobytes())
    return len(mesh.vertices), digest.digest()
