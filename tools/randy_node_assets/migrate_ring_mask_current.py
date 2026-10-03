"""Explicit, conservative migration of current-file Ring/Arc mask instances.

This module registers no handlers and never opens or saves a scene. Call audit()
before migrate(), with already-loaded canonical historical references. Linked
source files are read only. The caller owns loading the new asset, backing up
the working file, and deciding whether to remove unused retired datablocks.
"""

import json
import math
import re

import bpy


RADIAL_INPUTS = ("Inner Radius", "Ring Width", "Edge Softness")
ANGLE_INPUTS = ("Start Angle", "Sweep Angle")
OLD_SOURCES = {
    "tools/randy_node_assets/build_ring_mask.py",
    "tools/randy_node_assets/build_arc_mask.py",
}


def _value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bpy.types.ID):
        return (value.bl_rna.identifier, value.name_full,
                value.library.filepath if value.library else None)
    try:
        return tuple(_value(item) for item in value)
    except TypeError:
        raise ValueError("Unsupported node property value: " + repr(type(value)))


def _animated(tree):
    animation = tree.animation_data
    return bool(animation and (animation.action or animation.nla_tracks or animation.drivers))


def _interface(group, direction):
    return tuple((s.name, s.socket_type) for s in group.interface.items_tree
                 if s.item_type == "SOCKET" and s.in_out == direction)


def _socket(sockets, identifier):
    return next((s for s in sockets if s.identifier == identifier), None)


def _dependencies(group, found=None):
    found = {} if found is None else found
    pointer = group.as_pointer()
    if pointer in found:
        return found
    found[pointer] = group
    for node in group.nodes:
        nested = getattr(node, "node_tree", None)
        if nested is not None:
            _dependencies(nested, found)
    return found


def graph_signature(group, output_names=None, seen=None):
    """Calculation equality; layout, labels, colors and asset metadata excluded.

    Named internal nodes remain part of the proof. A renamed calculation is
    conservatively refused rather than treated as an unknown equivalent graph.
    Every used nested group is checked recursively; mutable shared data cannot
    bypass equality merely by preserving an outer group name.
    """
    seen = set() if seen is None else set(seen)
    if group.as_pointer() in seen:
        raise ValueError("Recursive node dependency cannot be migrated.")
    seen.add(group.as_pointer())
    if group.bl_idname != "ShaderNodeTree" or _animated(group):
        raise ValueError("Only unanimated Shader node groups can be migrated.")
    outputs = [n for n in group.nodes if n.bl_idname == "NodeGroupOutput" and n.is_active_output]
    if len(outputs) != 1:
        raise ValueError("Exactly one active Group Output is required.")
    output = outputs[0]
    names = output_names or tuple(s.name for s in group.interface.items_tree
                                  if s.item_type == "SOCKET" and s.in_out == "OUTPUT")
    endpoints = []
    pending = []
    for name in names:
        socket = output.inputs.get(name)
        if socket is None:
            raise ValueError("Required group output is missing: " + name)
        endpoints.append((name, tuple((l.from_node.name, l.from_socket.identifier)
                                     for l in socket.links)))
        pending.extend(l.from_node for l in socket.links)
    required = {}
    while pending:
        node = pending.pop()
        if node.name in required:
            continue
        required[node.name] = node
        pending.extend(l.from_node for s in node.inputs for l in s.links)
    records, cables = [], []
    fields = ("operation", "data_type", "interpolation_type", "clamp", "use_clamp",
              "from_instancer", "object", "uv_map", "attribute_name", "blend_type",
              "projection", "extension", "space", "vector_type", "distribution")
    for name, node in sorted(required.items()):
        record = [name, node.bl_idname, bool(node.mute),
                  tuple((field, _value(getattr(node, field))) for field in fields
                        if hasattr(node, field)),
                  tuple((s.identifier, s.name, _value(s.default_value)) for s in node.inputs
                        if hasattr(s, "default_value"))]
        if node.bl_idname == "ShaderNodeGroup":
            if node.node_tree is None:
                raise ValueError("A nested Shader group is missing.")
            used_outputs = tuple(sorted({l.from_socket.name for l in group.links
                                         if l.from_node == node
                                         and (l.to_node.name in required or l.to_node == output)}))
            record.append(graph_signature(node.node_tree, used_outputs, seen))
        elif node.bl_idname not in {"NodeGroupInput", "ShaderNodeMath", "ShaderNodeVectorMath",
                                   "ShaderNodeMapRange", "ShaderNodeSeparateXYZ", "ShaderNodeTexCoord",
                                   "ShaderNodeCombineXYZ", "NodeCombineBundle", "NodeSeparateBundle"}:
            raise ValueError("Unrecognized calculation node: " + node.bl_idname)
        if node.bl_idname in {"NodeCombineBundle", "NodeSeparateBundle"}:
            record.append(tuple((item.name, item.socket_type) for item in node.bundle_items))
            if hasattr(node, "define_signature"):
                record.append(bool(node.define_signature))
        records.append(tuple(record))
    for link in group.links:
        if link.from_node.name in required and link.to_node.name in required:
            cables.append((link.from_node.name, link.from_socket.identifier,
                           link.to_node.name, link.to_socket.identifier))
    return tuple(records), tuple(sorted(cables)), tuple(endpoints)


def _validate_target(target):
    if target is None or target.bl_idname != "ShaderNodeTree":
        raise ValueError("Load the new Ring Mask asset first.")
    expected = tuple((name, "NodeSocketFloat") for name in RADIAL_INPUTS + ANGLE_INPUTS)
    if _interface(target, "INPUT") != expected or _interface(target, "OUTPUT") != (
            ("Mask", "NodeSocketFloat"), ("Ring Data", "NodeSocketBundle")):
        raise ValueError("The new Ring Mask must expose five controls, Mask and Ring Data.")
    if target.name.split(".")[0] != "Ring Mask" or target.get("randy_asset_version") != "0.2.1":
        raise ValueError("Choose the validated multifunction Ring Mask 0.2.1.")
    graph_signature(target)


def _owners():
    trees = {}
    for group in bpy.data.node_groups:
        if group.bl_idname == "ShaderNodeTree":
            trees[group.as_pointer()] = group
    for collection in (bpy.data.materials, bpy.data.worlds, bpy.data.lights):
        for datablock in collection:
            tree = getattr(datablock, "node_tree", None)
            if tree is not None and tree.bl_idname == "ShaderNodeTree":
                trees[tree.as_pointer()] = tree
    return sorted(trees.values(), key=lambda tree: (tree.name_full, tree.as_pointer()))


def _looks_retired(group):
    return (group.get("randy_asset_source") in OLD_SOURCES
            or re.fullmatch(r"(?:Ring|Arc)\s*Mask(?:\.\d+)?", group.name, re.IGNORECASE) is not None)


def _plans(target, references):
    _validate_target(target)
    references = tuple(references)
    excluded = set(_dependencies(target))
    proofs = []
    for reference in references:
        excluded.update(_dependencies(reference))
        try:
            proofs.append((_interface(reference, "INPUT"), _interface(reference, "OUTPUT"),
                           graph_signature(reference), reference.name_full))
        except ValueError:
            continue
    plans, blocked = [], []
    for owner in _owners():
        # Retired source groups and reference fixtures are read-only inputs to
        # the migration even when their current-file datablocks are local.
        # Swap instances outside these groups; never rebuild the old sources.
        if owner.as_pointer() in excluded or _looks_retired(owner):
            continue
        for node in owner.nodes:
            old = getattr(node, "node_tree", None)
            if node.bl_idname != "ShaderNodeGroup" or old is None or old == target or not _looks_retired(old):
                continue
            record = {"owner": owner.name_full, "node": node.name, "group": old.name_full,
                      "library": old.library.filepath if old.library else None}
            try:
                if owner.library is not None or not owner.is_editable:
                    raise ValueError("Owner Shader tree is linked or read only.")
                if _animated(owner):
                    raise ValueError("Owner Shader tree is animated; socket-index remapping is not automatic.")
                if owner.as_pointer() in _dependencies(target):
                    raise ValueError("Replacement would create a recursive group.")
                inputs, outputs = _interface(old, "INPUT"), _interface(old, "OUTPUT")
                if inputs not in (tuple((name, "NodeSocketFloat") for name in RADIAL_INPUTS),
                                  tuple((name, "NodeSocketFloat") for name in RADIAL_INPUTS + ANGLE_INPUTS)):
                    raise ValueError("Unrecognized historical input interface.")
                if any(name not in {"Mask", "Ring Data"} for name, _kind in outputs):
                    raise ValueError("Unrecognized historical output interface.")
                signature = graph_signature(old)
                proof = next((label for ins, outs, graph, label in proofs
                              if inputs == ins and outputs == outs and signature == graph), None)
                if proof is None:
                    raise ValueError("Calculation differs from every supplied historical reference; preserve customized group.")
                values = {s.name: _value(s.default_value) for s in node.inputs if hasattr(s, "default_value")}
                if any(isinstance(value, float) and not math.isfinite(value) for value in values.values()):
                    raise ValueError("Non-finite input value cannot be preserved safely.")
                record["reference"] = proof
                plans.append((owner, node, old, values, record))
            except ValueError as exc:
                record["reason"] = str(exc)
                blocked.append(record)
    return plans, blocked


def audit(target, references):
    """Read-only report. References must be independently validated old groups."""
    plans, blocked = _plans(target, references)
    return {"target": target.name_full, "eligible": [p[4] for p in plans],
            "blocked": blocked, "eligible_count": len(plans), "blocked_count": len(blocked)}


def _external_links(owner):
    return [(l.from_node, l.from_socket.identifier, l.from_socket.name,
             l.to_node, l.to_socket.identifier, l.to_socket.name)
            for l in owner.links]


def _restore(plans, cables):
    changed = {node.as_pointer() for _owner, node, _old, _values, _record in plans}
    for owner, node, _old, values, _record in plans:
        for name, value in values.items():
            socket = node.inputs.get(name)
            if socket is None or not hasattr(socket, "default_value"):
                raise ValueError("Input disappeared during migration: " + name)
            socket.default_value = value
    for owner, links in cables:
        for link in list(owner.links):
            if link.from_node.as_pointer() in changed or link.to_node.as_pointer() in changed:
                owner.links.remove(link)
        for source, source_id, source_name, dest, dest_id, dest_name in links:
            if source.as_pointer() not in changed and dest.as_pointer() not in changed:
                continue
            output = (source.outputs.get(source_name) if source.as_pointer() in changed
                      else _socket(source.outputs, source_id))
            input_socket = (dest.inputs.get(dest_name) if dest.as_pointer() in changed
                            else _socket(dest.inputs, dest_id))
            if output is None or input_socket is None:
                raise ValueError("A connected socket disappeared during migration.")
            owner.links.new(output, input_socket)


def migrate(target, references, *, allow_partial=False):
    """Swap proven instances atomically; never modify the source groups.

    On failure every swapped group, original value and external cable is
    restored. No old datablock is deleted here, so rollback remains possible.
    The caller can explicitly remove unused retired groups afterward.
    """
    plans, blocked = _plans(target, references)
    if blocked and not allow_partial:
        raise ValueError("Migration blocked; inspect audit(): " + json.dumps(blocked))
    owners = {owner.as_pointer(): owner for owner, _n, _g, _v, _r in plans}
    cables = [(owner, _external_links(owner)) for owner in owners.values()]
    applied = []
    try:
        for plan in plans:
            _owner, node, _old, values, _record = plan
            applied.append(plan)
            node.node_tree = target
            if "Sweep Angle" not in values:
                node.inputs["Start Angle"].default_value = 0.0
                node.inputs["Sweep Angle"].default_value = 360.0
        _restore(plans, cables)
        for _owner, node, _old, values, _record in plans:
            for name, value in values.items():
                actual = _value(node.inputs[name].default_value)
                if actual != value:
                    raise ValueError("Input value changed during migration: " + name)
    except Exception:
        for _owner, node, old, _values, _record in reversed(applied):
            node.node_tree = old
        _restore(plans, cables)
        raise
    retired = {old.as_pointer(): old for _o, _n, old, _v, _r in plans}
    return {"target": target.name_full, "migrated_count": len(plans),
            "migrated": [p[4] for p in plans], "blocked": blocked,
            "unused_retired_groups": [group.name_full for group in retired.values()
                                      if group.users - int(group.use_fake_user) == 0],
            "saved_file": False, "linked_sources_modified": False}
