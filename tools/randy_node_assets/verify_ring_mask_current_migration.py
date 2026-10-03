"""Small factory fixture for explicit Ring Mask current-file migration.

This test uses only newly created materials and appended canonical assets. It
never opens a working file and does not render or register the RR Helper add-on.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import sys

import bpy


def module():
    path = Path(__file__).with_name("migrate_ring_mask_current.py")
    spec = importlib.util.spec_from_file_location("ring_current_migration", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def append(path, name):
    with bpy.data.libraries.load(str(path), link=False) as (available, requested):
        assert name in available.node_groups
        requested.node_groups = [name]
    return requested.node_groups[0]


def material(name):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    result.node_tree.nodes.clear()
    return result.node_tree


def instance(tree, group, values):
    node = tree.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    for name, value in values.items():
        node.inputs[name].default_value = value
    return node


def link_state(tree):
    return sorted((l.from_node.name, l.from_socket.identifier,
                   l.to_node.name, l.to_socket.identifier) for l in tree.links)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--arc", required=True)
    parser.add_argument("--radial", required=True)
    parser.add_argument("--report")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Use an isolated background factory process.")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    helper = module()
    target = append(Path(args.target), "Ring Mask")
    radial = append(Path(args.radial), "Ring Mask")
    arc = append(Path(args.arc), "Arc Mask")
    references = (radial, arc)
    before = [helper.graph_signature(item) for item in references]
    local_radial, local_arc = radial.copy(), arc.copy()
    tree = material("Migration fixture")
    radial_values = {"Inner Radius": .21, "Ring Width": .031, "Edge Softness": .002}
    arc_values = dict(radial_values, **{"Start Angle": -39.7, "Sweep Angle": 146.3})
    ring = instance(tree, local_radial, radial_values)
    angle = instance(tree, local_arc, arc_values)
    ring.label = "User label"
    ring.location = (131, -87)
    ring.width = 312
    presentation = (ring.name, ring.label, tuple(ring.location), ring.width, ring.select)
    value = tree.nodes.new("ShaderNodeValue")
    value.outputs[0].default_value = .125
    tree.links.new(value.outputs[0], ring.inputs["Ring Width"])
    math_node = tree.nodes.new("ShaderNodeMath")
    math_node.operation = "MAXIMUM"
    tree.links.new(ring.outputs["Mask"], math_node.inputs[0])
    tree.links.new(angle.outputs["Mask"], math_node.inputs[1])
    cables = link_state(tree)

    nested = bpy.data.node_groups.new("User nested shader", "ShaderNodeTree")
    nested_node = instance(nested, local_radial, radial_values)
    customized = local_radial.copy()
    next(n for n in customized.nodes if n.bl_idname == "ShaderNodeMath").operation = "ADD"
    custom_tree = material("Customized source")
    custom_node = instance(custom_tree, customized, radial_values)
    audit = helper.audit(target, references)
    assert audit["eligible_count"] == 3 and audit["blocked_count"] == 1, audit
    assert "differs" in audit["blocked"][0]["reason"]
    try:
        helper.migrate(target, references)
    except ValueError:
        pass
    else:
        raise AssertionError("Strict migration must refuse the customized graph.")
    assert ring.node_tree == local_radial and angle.node_tree == local_arc
    assert link_state(tree) == cables
    report = helper.migrate(target, references, allow_partial=True)
    assert report["migrated_count"] == 3
    assert ring.node_tree == target and angle.node_tree == target and nested_node.node_tree == target
    assert custom_node.node_tree == customized
    assert (ring.name, ring.label, tuple(ring.location), ring.width, ring.select) == presentation
    assert link_state(tree) == cables
    assert abs(ring.inputs["Inner Radius"].default_value - .21) < 1e-6
    assert ring.inputs["Start Angle"].default_value == 0
    assert ring.inputs["Sweep Angle"].default_value == 360
    assert abs(angle.inputs["Start Angle"].default_value + 39.7) < 1e-5
    assert abs(angle.inputs["Sweep Angle"].default_value - 146.3) < 1e-5
    assert ring.inputs["Ring Width"].is_linked
    assert [helper.graph_signature(item) for item in references] == before

    rollback_tree = material("Rollback fixture")
    rollback_node = instance(rollback_tree, local_radial, radial_values)
    sink = rollback_tree.nodes.new("ShaderNodeMath")
    rollback_tree.links.new(rollback_node.outputs["Mask"], sink.inputs[1])
    rollback_cables = link_state(rollback_tree)
    original_restore = helper._restore
    calls = [0]

    def inject_failure(plans, links):
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("Injected external-cable restoration failure")
        return original_restore(plans, links)

    helper._restore = inject_failure
    try:
        helper.migrate(target, references, allow_partial=True)
    except RuntimeError as exc:
        assert "Injected" in str(exc)
    else:
        raise AssertionError("Injected migration failure must not be swallowed.")
    finally:
        helper._restore = original_restore
    assert rollback_node.node_tree == local_radial
    assert abs(rollback_node.inputs["Inner Radius"].default_value - .21) < 1e-6
    assert link_state(rollback_tree) == rollback_cables
    assert [helper.graph_signature(item) for item in references] == before
    result = {"passed": True, "tests": [
        "radial_and_arc_instances_migrate", "radial_defaults_full_sweep",
        "arc_angles_preserved", "duplicate_math_socket_links_preserved",
        "presentation_preserved", "nested_editable_owner_supported",
        "customized_source_refused_before_swap", "partial_migration_explicit",
        "source_graphs_unchanged", "injected_failure_rolls_back_all_links_and_values"],
        "rendered": False, "production_file_opened": False}
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("RING_MASK_MIGRATION_CHECK " + json.dumps(result))


if __name__ == "__main__":
    main()
