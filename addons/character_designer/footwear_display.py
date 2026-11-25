"""Fit the two foot handles in the moving, pre-roll coordinate system of the leg."""
import math

import bpy
from mathutils import Matrix, Vector

from . import character_setup, foot_controls as feet, foot_sole_display as sole, limb_ik as limb

FOLLOW = 'CD Heel Dial Foot Space'


def reference(context, rig, record):
    mapping = character_setup._mapping(character_setup.settings(context), rig)
    saved = (record.get('footwear_display') or {}).get('reference')
    if saved and not (mapping and mapping.footwear):
        # Older fits stored the name only. An explicit selection can identify
        # a standalone mesh; an old name alone cannot prove its character.
        candidate = context.scene.objects.get(saved)
        if (candidate and candidate.type == 'MESH' and (candidate.parent == rig
                or any(m.type == 'ARMATURE' and m.object == rig for m in candidate.modifiers))):
            return candidate
        raise limb.LimbIKError(f'Choose footwear for the {record["side"]} foot to replace its saved reference "{saved}".')
    shoe = character_setup.footwear_reference(context, rig)
    if shoe is None:
        # Earlier generated feet retained the chosen footwear in their visual fit.
        previous = record.get('roll_visual_fit') or {}
        if previous.get('source') == 'SHOE':
            candidate = context.scene.objects.get(previous.get('reference', ''))
            if candidate and any(m.type == 'ARMATURE' and m.object == rig for m in candidate.modifiers):
                shoe = candidate
    return shoe


def _outsole(context, rig, record, shoe, rotation, *, toe_section=None):
    """Evaluate the mirrored sole at bind geometry without changing the displayed shoe."""
    foot = record['chain'][2]
    mods = [m for m in shoe.modifiers if m.type == 'ARMATURE' and m.show_viewport and m.object]
    if any(m.object != rig for m in mods):
        raise limb.LimbIKError('The selected footwear belongs to another armature.')
    world = shoe.evaluated_get(context.evaluated_depsgraph_get()).matrix_world.copy()
    if shoe.parent_type == 'BONE' and shoe.parent:
        if shoe.parent != rig:
            raise limb.LimbIKError('Choose footwear attached to this character.')
        parent = rig.pose.bones[shoe.parent_bone]
        pose = rig.matrix_world @ parent.matrix
        world = rig.matrix_world @ parent.bone.matrix_local @ pose.inverted() @ world
    sample = shoe.copy()
    try:
        sample.animation_data_clear()
        sample.parent = None
        sample.constraints.clear()
        for mod in list(sample.modifiers):
            if mod.type == 'ARMATURE':
                sample.modifiers.remove(mod)
        sample.matrix_world = world
        context.scene.collection.objects.link(sample)
        context.view_layer.update()
        edges = [] if toe_section is not None else None
        points = feet._shoe_points(context, rig, foot, record['toe'], sample, rotation, section_edges=edges)
    finally:
        bpy.data.objects.remove(sample, do_unlink=True)
    low = min(p.z for p in points)
    length = max(p.y for p in points) - min(p.y for p in points)
    band = [p for p in points if p.z <= low + 0.17 * length]
    lo = Vector([min(p[i] for p in band) for i in range(3)])
    hi = Vector([max(p[i] for p in band) for i in range(3)])
    if min(hi.x - lo.x, hi.y - lo.y) < 1.e-5:
        raise limb.LimbIKError('The footwear has no measurable outsole for this side.')
    if toe_section is not None:
        from . import toe_cap_display
        to_rig = rig.data.bones[foot].matrix_local @ rotation.to_4x4()
        toe_y = rig.data.bones[record['toe']].head_local.y
        toe_section.extend(toe_cap_display.section(points, edges, to_rig, toe_y, hi.y - lo.y))
    return lo, hi


def _fit_outline(target, rotation, lo, hi):
    shape = target.custom_shape
    if shape is None or shape.type != 'MESH' or shape.modifiers or shape.data.shape_keys:
        raise limb.LimbIKError('Use a planar Foot IK mesh outline for the footwear fit.')
    verts = [v.co for v in shape.data.vertices]
    if len(verts) < 3:
        raise limb.LimbIKError('The Foot IK outline is empty.')
    a = Vector([min(v[i] for v in verts) for i in range(3)])
    b = Vector([max(v[i] for v in verts) for i in range(3)])
    if min(b.x-a.x, b.y-a.y) < 1.e-6:
        raise limb.LimbIKError('The footwear fit needs a planar XY sole outline.')
    if b.z-a.z > 1.e-5:
        # The bundled shared FOOT wire has a small intentional depth variation.
        # Match its original float32 coordinates and edges before allowing it.
        armature_id = target.id_data.data.get(limb.ARMATURE_ID_KEY)
        template, edges = limb._widget_geometry('FOOT')
        if (not armature_id or not limb._owned(shape, armature_id, role='WIDGET')
                or not limb._owned(shape.data, armature_id, role='WIDGET_DATA')
                or shape.get(limb.KIND_KEY) != 'FOOT' or shape.data.get(limb.KIND_KEY) != 'FOOT'
                or shape.data.polygons or len(verts) != len(template) or len(shape.data.edges) != len(edges)
                or any(tuple(v) != tuple(Vector(co)) for v, co in zip(verts, template))
                or {tuple(sorted(e.vertices)) for e in shape.data.edges} != {tuple(sorted(e)) for e in edges}):
            raise limb.LimbIKError('The footwear fit needs a planar XY sole outline.')
    previous = limb._custom_shape_state_matrix(limb._control_visual_state(target))
    axes = rotation.transposed() @ previous.to_3x3()
    size = target.bone.length if target.use_custom_shape_bone_size else 1.
    margin = (hi.x-lo.x) * 0.025
    scale = Vector((math.copysign((hi.x-lo.x+2*margin)/(b.x-a.x), axes[0][0]),
                    math.copysign((hi.y-lo.y+2*margin)/(b.y-a.y), axes[1][1]),
                    math.copysign(max(abs(target.custom_shape_scale_xyz.z)*size, 1.e-6), axes[2][2])))
    center = (lo+hi)*0.5
    center.z = lo.z
    raw_center = (a+b)*0.5
    return dict(version=1, translation=list(rotation @ (center - scale*raw_center)),
                rotation=list(rotation.to_euler()), scale=list(scale / size))


def _branch_pose(branch, foot, unrolled, rest, depsgraph):
    from . import natural_bind_graph as graph
    source = branch.get('source')
    frames = branch.get('frames')
    frame = frames.get(foot) if frames else None
    follow = frame.constraints.get(graph.FOLLOW) if frame else None
    if (not source or source.type != 'ARMATURE' or not follow or follow.type != 'CHILD_OF'
            or follow.target != source or follow.subtarget != foot):
        return None
    raw = source.evaluated_get(depsgraph).pose.bones.get(unrolled)
    if raw is None:
        return None
    frozen = branch.get('neutral_source')
    if 'neutral_source' in branch or 'neutral_signature' in branch:
        if not frozen or frozen.type != 'ARMATURE':
            return None
        neutral = frozen.evaluated_get(depsgraph).pose.bones.get(foot)
        inverse = neutral.matrix.inverted(None) if neutral else None
        if inverse is None:
            return None
        offset = inverse @ rest
    else:
        offset = follow.inverse_matrix
    return raw.matrix @ offset


def _correct_display(rig, evaluated, helper, depsgraph):
    from . import natural_bind_graph as graph
    basis = Matrix.Identity(4)
    rec = rig.get(graph.KEY)
    if rec and rec['enabled'] and rec['attached']:
        sources = helper.bone['footwear_sources']
        if len(sources) != 2:
            return
        foot, raw = sources
        foot_bone = evaluated.pose.bones.get(foot)
        raw_bone = evaluated.pose.bones.get(raw)
        if foot_bone is None or raw_bone is None:
            return
        rest = foot_bone.bone.matrix_local
        stable = rec.get('stable')
        mix = stable['mix'] if stable else 0.
        if mix < 1.:
            desired = _branch_pose(rec, foot, raw, rest, depsgraph)
            if desired is None:
                return
        if mix > 0.:
            alternate = _branch_pose(stable, foot, raw, rest, depsgraph)
            if alternate is None:
                return
            desired = alternate if mix >= 1. else desired.lerp(alternate, mix)
        # Apply the same live bind correction on both sides of zero roll.
        # This also covers keyed Direct/Stable offsets and neutral-reference previews.
        basis = raw_bone.matrix.inverted_safe() @ desired
        basis = Matrix.LocRotScale(*basis.decompose())
    if max(abs(a-b) for ra,rb in zip(helper.matrix_basis, basis) for a,b in zip(ra,rb)) > 2.e-6:
        helper.matrix_basis = basis


def update(scene, depsgraph):
    if depsgraph is None or not depsgraph.id_type_updated('OBJECT'):
        return
    for change in depsgraph.updates:
        try:
            rig = getattr(change.id, 'original', None)
        except ReferenceError:
            continue  # Undo or removal invalidated the update's ID.
        if not isinstance(rig,bpy.types.Object) or rig.type != 'ARMATURE' or rig.mode == 'EDIT':
            continue
        if ('character_designer_footwear_display' not in rig or rig.data.pose_position == 'REST'
                or rig.name not in scene.objects):
            continue
        evaluated = rig.evaluated_get(depsgraph)
        for name in rig['character_designer_footwear_display'].values():
            try:
                helper = rig.pose.bones.get(name)
                if (helper and helper.bone.get(feet.ROLE_KEY) == 'SOLE_DISPLAY'
                        and 'footwear_sources' in helper.bone):
                    _correct_display(rig, evaluated, helper, depsgraph)
            except ReferenceError:
                continue  # A removed RNA reference only invalidates this side.


def register():
    bpy.app.handlers.persistent(update)
    for handlers in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.frame_change_post):
        if update not in handlers:
            handlers.append(update)


def unregister():
    for handlers in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.frame_change_post):
        if update in handlers:
            handlers.remove(update)


def forget(rig, side):
    key = 'character_designer_footwear_display'
    if key in rig:
        rig[key].pop(side, None)
        if not len(rig[key]):
            del rig[key]


def fit(context, rig, record, shoe=None):
    """Attach both handles to the pre-roll shoe. Preserve native animator inputs."""
    from copy import deepcopy
    from . import toe_cap_display
    from . import foot_roll_hinge as hinge, foot_roll_hinge_plant as plant
    raw = record['bones'].get('UNROLLED_FOOT')
    if not raw:
        return False
    if record.get('footwear_display') and shoe is None:
        return False
    values = feet._all_records(rig)
    side = record['side']
    record = deepcopy(record)
    target = rig.pose.bones[record['target']]
    end = rig.pose.bones[record['chain'][2]]
    roll = rig.pose.bones[record['roll']]
    root = record['bones'].get('SOLE_DISPLAY')
    installed = bool(root)
    if (target.custom_shape_transform not in (None, end, rig.pose.bones.get(root or ''))
            or roll.custom_shape_transform is not None or roll.parent != target
            or bool(roll.constraints.get(FOLLOW)) != installed):
        raise limb.LimbIKError('The foot controls use a custom display reference.')
    sole._guard_offsets(target)
    feet._visual_animation_guard(rig, roll)
    saved = sole.capture(target)
    names = tuple(rig.pose.bones.keys())
    held = hinge._held_inputs(rig, names)
    pose = {n:rig.pose.bones[n].matrix.copy() for n in names if n != roll.name}
    before = rig.data[feet.RECORD_KEY]
    added = plant.new_record()
    old_basis = None
    toe_saved = None
    try:
        rotation, _, _ = feet._foot_display_frame(rig, end.name, record['toe'])
        toe_section = []
        bounds = _outsole(context, rig, record, shoe, rotation, toe_section=toe_section) if shoe else None
        if not installed:
            root = 'MCH_sole_display.' + side
            dial = 'MCH_dial_display.' + side
            with plant.editing(context, rig) as bones:
                plant.create(bones, added, 'SOLE_DISPLAY', root, bones[raw], raw)
                plant.create(bones, added, 'DIAL_DISPLAY', dial, bones[roll.name], root)
            parent = plant.register(rig, record, added, 'SOLE_DISPLAY')
            child = plant.register(rig, record, added, 'DIAL_DISPLAY')
            parent.bone['footwear_sources'] = [end.name,raw]
            feet._add_constraint(added, child, 'COPY_ROTATION', 'CD Dial Angle', rig,
                                 subtarget=record['bones']['BALL_PIVOT'], owner_space='LOCAL',target_space='LOCAL',
                                 mix_mode='AFTER',use_x=True,use_y=False,use_z=False)
            feet._add_constraint(added, roll, 'COPY_TRANSFORMS', FOLLOW, rig, subtarget=dial,
                                 owner_space='POSE',target_space='POSE',mix_mode='REPLACE')
            plant.merge(record, added)
        parent = rig.pose.bones[root]
        child = rig.pose.bones[record['bones']['DIAL_DISPLAY']]
        old_basis = child.matrix_basis.copy()
        if target.custom_shape_transform != parent:
            old = target.custom_shape_transform or target
            if shoe is None and target.bone.get(sole.KEY) == 1 and old == target:
                sole._correct_fit(target, sole._level_in_rest(target))
            limb._retarget_custom_shape_frame(target, parent, current_reference_matrix=old.bone.matrix_local,
                                              previous_reference_matrix=parent.bone.matrix_local)
        if bounds:
            lo, hi = bounds
            fit_state = _fit_outline(target, rotation, lo, hi)
            limb._apply_control_visual_state(target, fit_state)
            target.bone[limb.CONTROL_VISUAL_DEFAULT_KEY] = limb._canonical_json(fit_state)
            wire = roll.custom_shape
            if not wire or wire.type != 'MESH' or not wire.data.vertices:
                raise limb.LimbIKError('The heel dial has no measurable mesh outline.')
            linear = roll.custom_shape_rotation_euler.to_matrix() @ Matrix.Diagonal(roll.custom_shape_scale_xyz)
            size = roll.bone.length if roll.use_custom_shape_bone_size else 1.
            points = [linear @ (v.co * size) for v in wire.data.vertices]
            length = hi.y-lo.y
            center = Vector(((lo.x+hi.x)*0.5, lo.y-length*.07-max(p.y for p in points),
                             lo.z+length*.08-min(p.z for p in points)))
            dial_local = rotation.to_4x4()
            dial_local.translation = rotation @ center
            child.matrix_basis = child.bone.matrix_local.inverted() @ parent.bone.matrix_local @ dial_local
            toe_saved = toe_cap_display.fit(rig, record, rotation, toe_section)
        record['footwear_display'] = dict(reference=shoe.name if shoe else None)
        target.bone[sole.KEY] = 3
        marker = 'character_designer_footwear_display'
        if marker not in rig:
            rig[marker] = {}
        rig[marker][side] = root
        feet._write_records(rig, dict(values, **{side:record}))
        feet._update(context, rig)
        # New hidden display calibration belongs to the fitter on a repeat fit.
        success_inputs = held
        if installed:
            updated = hinge._held_inputs(rig, (child.name, parent.name))
            success_inputs = dict(held, pose={**held['pose'], **updated['pose']})
            pose.pop(child.name, None)
            pose.pop(parent.name, None)
        hinge._restore_held_inputs(context, rig, success_inputs, names)
        feet._verify_pose(rig, pose)
        return True
    except Exception:
        if toe_saved:
            toe_cap_display.restore(rig, record, toe_saved)
        if not installed:
            forget(rig, side)
        elif old_basis is not None:
            rig.pose.bones[record['bones']['DIAL_DISPLAY']].matrix_basis = old_basis
        sole.restore(rig.pose.bones[record['target']], saved)
        plant.rollback(context, rig, added)
        rig.data[feet.RECORD_KEY] = before
        feet._update(context, rig)
        hinge._restore_held_inputs(context, rig, held, names)
        raise

