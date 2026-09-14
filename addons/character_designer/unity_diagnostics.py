"""Read-only diagnostics shared by the Unity exporter and source-mesh locator.

These indices belong to ``obj.data``. They deliberately do not refer to an
evaluated mesh: Mirror, Subdivision and FBX may change or duplicate indices.
The caller must flush Edit Mode changes before inspecting the source mesh.
"""

from __future__ import annotations


CONTROL_OWNERS = frozenset({
    'limb_ik', 'foot_controls', 'torso_controls', 'eye_controls',
    'spine_ik_fk', 'root_control',
})


def is_generated_control(bone):
    """Use the exporter's ownership/name rules without hiding native deformers."""
    if bone.get('character_designer_owner') in CONTROL_OWNERS:
        return True
    if bone.use_deform:
        return False
    name = bone.name.rsplit(':', 1)[-1].upper()
    return name.startswith(('CTRL_', 'CTRL-', 'MCH_', 'MCH-',
                            'ORG_', 'ORG-', 'ORI_', 'ORI-'))


def unweighted_vertex_indices(obj, retained_only=True):
    """Return source vertices lacking positive weights on active deform bones.

    ``None`` means no enabled valid Armature modifier exists (or this is not a
    mesh), rather than implying the object has a complete skin. An enabled rig
    with no matching deform groups makes every vertex unweighted. Inert vertex
    groups, disabled rigs, and generated controls excluded from export cannot
    satisfy the diagnostic. ``retained_only=False`` additionally accepts owned
    deform controls, for callers inspecting a skin before export filtering.

    The function never evaluates modifiers or mutates geometry, shape keys,
    selection, weights, mode, or the dependency graph.
    """
    if obj is None or obj.type != 'MESH':
        return None
    armatures = {modifier.object for modifier in obj.modifiers
                 if modifier.type == 'ARMATURE' and modifier.show_viewport
                 and modifier.object is not None
                 and modifier.object.type == 'ARMATURE'}
    if not armatures:
        return None
    deform_names = {bone.name for rig in armatures for bone in rig.data.bones
                    if bone.use_deform
                    and (not retained_only or not is_generated_control(bone))}
    indices = {group.index for group in obj.vertex_groups
               if group.name in deform_names}
    return [vertex.index for vertex in obj.data.vertices
            if not any(entry.group in indices and entry.weight > 1e-8
                       for entry in vertex.groups)]
