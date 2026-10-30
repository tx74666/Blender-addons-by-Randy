"""Private selected-strand Hair acceptance; old pinned harness stays unchanged.

Run through the trusted launcher on one complete frozen projection.  No artist
file is opened.  Partial versions must preserve the native author inputs of all
unselected chains, rather than merely reproduce their pose on a sample grid.
"""
from pathlib import Path
import copy
import hashlib
import json
import math
import sys
import traceback
import unittest
from unittest.mock import patch

import bpy
import bmesh
from mathutils import Vector

REPO = Path(__file__).resolve().parents[1]
PINNED = REPO / 'tests/validate_hair_resegment_blender52.py'
PINNED_SHA = '0d29f26abfda2e172eeacf8fcc4c2c549f4ff23abe71c758ecc264c224620166'
digest = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
assert digest(PINNED) == PINNED_SHA
prefix = PINNED.read_text(encoding='utf-8').split('\nresult = None\n', 1)[0]
if globals().get('GUI', False):
    prefix = prefix.replace('assert bpy.app.background and not bpy.data.filepath and not args.report.exists()',
                            'assert not bpy.app.background and not bpy.data.filepath and not args.report.exists()')
scope = {'__file__': str(PINNED), '__name__': 'selected_hair_pinned_fixture'}
exec(compile(prefix, str(PINNED), 'exec'), scope)
args, cd, service, binding, hair, variants, retarget, registry, profiles, PACKAGE, manifest = (
    scope[name] for name in ('args', 'cd', 'service', 'binding', 'hair', 'variants',
                            'retarget', 'registry', 'profiles', 'PACKAGE', 'manifest'))
from character_designer import hair_resegment_selection as selection
from character_designer import hair_resegment_ui as ui
from test_hair_bones_rig_blender import activate, make_armature, make_hair

METRICS = {}


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def fresh_main():
    assert bpy.ops.wm.read_factory_settings(use_empty=True) == {'FINISHED'}
    assert not bpy.data.objects and not bpy.data.actions and not bpy.data.shape_keys
    assert hasattr(bpy.types.WindowManager, 'character_designer_hair_bones')


def selected_fixture(*, rich=True, nla=False, locked=(), automatic_pairs=True):
    fresh_main()
    rig = make_armature('Selected Character')
    rig.data.bones['spine.006'].name = 'Head'
    source, old_plans = make_hair('Three Strand Hair', strands=3)
    # Real independent full geometry: A/B are reflected coordinates, C is an
    # asymmetric third strand.  No Mirror modifier silently expands selection.
    for index in range(21, 42):
        first = source.data.vertices[index - 21].co
        source.data.vertices[index].co = (-first.x, first.y, first.z)
    for index in range(42, 63):
        source.data.vertices[index].co.y += .11 + .003 * (index - 42)
    source.data.update()
    # Match the existing binding fixture's genuine cap construction rather than
    # feeding its hand-written pre-discovery plans into stricter binding guards.
    from character_designer import hair_bones_topology as topology
    bm = bmesh.new()
    bm.from_mesh(source.data)
    bm.verts.ensure_lookup_table()
    bridges = [face for face in bm.faces if len({vertex.index // 21 for vertex in face.verts}) > 1]
    assert len(bridges) == 2
    for bridge in bridges:
        border = list(bridge.verts)
        center = sum((vertex.co for vertex in border), Vector()) / len(border)
        center.z += .12
        bm.faces.remove(bridge)
        cap = bm.verts.new(center)
        for index, vertex in enumerate(border):
            bm.faces.new((vertex, border[(index + 1) % len(border)], cap))
    bm.to_mesh(source.data)
    bm.free()
    bm = bmesh.new()
    bm.from_mesh(source.data)
    for domain in (bm.verts, bm.edges, bm.faces):
        domain.ensure_lookup_table()
        domain.index_update()
    allowed, edges, adjacency = topology._visible_graph(bm)
    discovered = topology._discover(source, bm, allowed, edges, adjacency, set())
    plans = sorted((copy.deepcopy(plan) for plan in discovered), key=lambda plan: min(plan['vertices']))
    bm.free()
    assert len(plans) == 3, 'The private fixture must authentically discover all three independent chains.'
    for index, plan in enumerate(plans):
        plan['signature'] = ('independent-left', 'independent-right', 'asymmetric-tail')[index]
    source['artist_note'] = 'Three independently authored Hair chains'
    source.vertex_groups.new(name='Artist Mask').add([0, 6, 21, 42, 62], .73, 'REPLACE')
    source.vertex_groups['Artist Mask'].lock_weight = True
    source.shape_key_add(name='Basis')
    detail = source.shape_key_add(name='Artist Detail')
    detail.data[-1].co.y += .021
    detail.value = .35
    activate(source)
    binding.bind_hair(bpy.context, source, plans, bone_count=4, armature=rig)
    record = hair._read_records(source)
    assert len(record['chains']) == 3
    if automatic_pairs:
        configured = registry.initialize(source)
    else:
        # A fresh, fully authenticated binding may start without automatic
        # pairing. This does not replace or erase any existing proof.
        configured, unused_record, unused_geometry = registry._live(source, check_layers=True)
        configured = registry._commit(source, configured)
    profiles.initialize(source, registry=configured)
    for index, strand in enumerate(configured['strands']):
        profiles.update(source, strand['strand_id'], group=('FRONT', 'SIDE', 'BACK')[index],
                        overrides={'damping': .64 + index * .08, 'gravity': .9 + index * .2},
                        registry=configured)
    for index, chain in enumerate(record['chains']):
        vertex = chain['vertices'][-1]
        for name in chain['bones']:
            source.vertex_groups[name].remove([vertex])
        source.vertex_groups[chain['bones'][0]].add([vertex], .31 + .01 * index, 'REPLACE')
        source.vertex_groups[chain['bones'][-1]].add([vertex], .42, 'REPLACE')
        for name in chain['bones']:
            rig.data.bones[name]['ArtistBoneNote'] = f'chain-{index}'
            pose = rig.pose.bones[name]
            pose['ArtistPoseControl'] = .25 + index * .07
            pose.rotation_mode = 'XYZ'
            pose.lock_location = (False, index == 2, False)
            pose.lock_rotation = (False, False, index == 2)
            pose.lock_scale = (index == 2, False, False)
            source.vertex_groups[name].lock_weight = index in locked
    action = bpy.data.actions.new('Three Strand Authored Action')
    action.use_fake_user = True
    unused = action.slots.new(id_type='OBJECT', name='Unrelated unused slot')
    slot, bag = retarget._new_channelbag(action, rig)
    for index, chain in enumerate(record['chains']):
        name = chain['bones'][0]
        retarget._write_curve(bag, rig.pose.bones[name].path_from_id('rotation_euler'),
                             index % 3, [1., 3.25, 8.], [.01 * index, .13 + .04 * index, -.08])
        curve = bag.fcurves[-1]
        # Native Bezier handles are deliberately nontrivial author input; an
        # unselected strand cannot be called preserved after integer rebaking.
        for key in curve.keyframe_points:
            key.interpolation = 'BEZIER'
            key.handle_left_type = key.handle_right_type = 'FREE'
            key.handle_left = (key.co.x - .31, key.co.y - .021)
            key.handle_right = (key.co.x + .47, key.co.y + .032)
    retarget._write_curve(bag, rig.pose.bones['Head'].path_from_id('rotation_euler'),
                         1, [1., 4., 8.], [0., .08, -.03])
    retarget._write_curve(bag, 'location', 2, [1., 8.], [.015, .04])
    ad = rig.animation_data_create()
    ad.action, ad.action_slot = action, slot
    # An unused slot with a path absent on this rig must not be resolved or
    # rewritten by selection, sampling or current-author protection.
    unused_bag = action.layers[0].strips[0].channelbags.new(unused)
    retarget._write_curve(unused_bag, '["OtherOwnerOnly"]', 0, [1., 8.], [.2, .7])
    if nla:
        overlay = bpy.data.actions.new('Three Strand Native NLA Overlay')
        overlay.use_fake_user = True
        overlay.slots.new(id_type='OBJECT', name='Unused overlay slot')
        overlay_slot, overlay_bag = retarget._new_channelbag(overlay, rig)
        for index, chain in enumerate(record['chains']):
            retarget._write_curve(overlay_bag, rig.pose.bones[chain['bones'][-1]].path_from_id('rotation_euler'),
                                 0, [1., 4., 8.], [.01, .06 + index * .02, -.01])
        track = ad.nla_tracks.new()
        track.name = 'Unchanged Authored NLA Track'
        strip = track.strips.new('Unchanged scaled repeated clip', 1, overlay)
        strip.action_slot = overlay_slot
        strip.blend_type, strip.influence, strip.scale, strip.repeat = 'ADD', .31, 1.25, 1.3
        ad.use_nla = True
    if rich:
        target = rig.pose.bones[record['chains'][2]['bones'][0]]
        constraint = target.constraints.new('LIMIT_ROTATION')
        constraint.name = 'Unselected Authored Limit'
        constraint.owner_space = 'LOCAL'
        constraint.use_limit_x, constraint.min_x, constraint.max_x = True, -.17, .19
        constraint.influence = .63
        rig['UnselectedDriverControl'] = .75
        driver = target.driver_add('scale', 2)
        driver.driver.type = 'SCRIPTED'
        driver.driver.expression = '1 + .05 * strength'
        variable = driver.driver.variables.new()
        variable.name, variable.type = 'strength', 'SINGLE_PROP'
        variable.targets[0].id = rig
        variable.targets[0].data_path = '["UnselectedDriverControl"]'
    bpy.context.scene.frame_set(3, subframe=.25)
    rig.pose.bones[record['chains'][0]['bones'][0]].rotation_euler.x += .041
    rig.pose.bones['Arm'].rotation_mode = 'XYZ'
    rig.pose.bones['Arm'].rotation_euler.x = .17
    activate(rig, 'POSE')
    bpy.context.window_manager.character_designer_hair_bones.source = source
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
    bpy.context.view_layer.update()
    return source, rig, hair._read_records(source)


def choose(source, signatures, *, every_bone=False):
    record = hair._read_records(source)
    rig = source[hair.RIG_KEY]
    activate(rig, 'POSE')
    wanted = set(signatures)
    for pose in rig.pose.bones:
        pose.select = False
    names = []
    for chain in record['chains']:
        if chain['signature'] in wanted:
            names.extend(chain['bones'] if every_bone else [chain['bones'][len(chain['bones']) // 2]])
    for name in names:
        rig.pose.bones[name].select = True
    rig.data.bones.active = rig.data.bones[names[0]] if names else None
    bpy.context.window_manager.character_designer_hair_bones.source = source
    return tuple(names)


def normalized(value, source, rig):
    if value is source:
        return '$SOURCE'
    if value is rig:
        return '$RIG'
    if value is source.data:
        return '$MESH'
    if value is rig.data:
        return '$ARMATURE'
    if source.data.shape_keys and value is source.data.shape_keys:
        return '$KEYS'
    if isinstance(value, bpy.types.ID):
        return ('EXTERNAL_ID', value.bl_rna.identifier, value.name_full)
    if type(value) in (str, int, float, bool) or value is None:
        return value
    if hasattr(value, 'items'):
        return {str(key): normalized(item, source, rig) for key, item in value.items()
                if key != service.COPY_KEY}
    try:
        return tuple(normalized(item, source, rig) for item in value)
    except TypeError:
        return repr(value)


def native_properties(value, source, rig, *, skip=()):
    result = {}
    for prop in value.bl_rna.properties:
        if prop.identifier in {'rna_type', *skip} or prop.type == 'COLLECTION':
            continue
        if prop.is_runtime:
            continue
        try:
            result[prop.identifier] = normalized(getattr(value, prop.identifier), source, rig)
        except (AttributeError, TypeError):
            pass
    return result


def driver_snapshot(owner, source, paths, *, rig=None):
    rig = owner if rig is None else rig
    ad = owner.animation_data
    rows = []
    for curve in ad.drivers if ad else ():
        if not any(curve.data_path.startswith(path) for path in paths):
            continue
        rows.append(dict(path=curve.data_path, index=curve.array_index,
            curve=native_properties(curve, source, rig, skip=('group', 'driver')),
            driver=native_properties(curve.driver, source, rig),
            variables=[dict(native=native_properties(variable, source, rig),
                targets=[native_properties(target, source, rig) for target in variable.targets])
                       for variable in curve.driver.variables]))
    return rows


def chain_author(source, signature):
    record = hair._read_records(source)
    chain = next(c for c in record['chains'] if c['signature'] == signature)
    rig = source[hair.RIG_KEY]
    names = set(chain['bones'])
    points = []
    for name in chain['bones']:
        bone, pose = rig.data.bones[name], rig.pose.bones[name]
        points.append(dict(name=name, rest=hair._bone_state(bone),
            deform=bone.use_deform, inherit_scale=bone.inherit_scale,
            inherit_rotation=bone.use_inherit_rotation, local_location=bone.use_local_location,
            bone_props=normalized(bone, source, rig), pose_props=normalized(pose, source, rig),
            rotation_mode=pose.rotation_mode, location=tuple(pose.location),
            rotation_euler=tuple(pose.rotation_euler), rotation_quaternion=tuple(pose.rotation_quaternion),
            rotation_axis_angle=tuple(pose.rotation_axis_angle), scale=tuple(pose.scale),
            locks=(tuple(pose.lock_location), tuple(pose.lock_rotation), tuple(pose.lock_scale),
                   pose.lock_rotation_w, pose.lock_rotations_4d),
            constraints=[native_properties(item, source, rig) for item in pose.constraints]))
    paths = [rig.pose.bones[name].path_from_id() for name in names]
    return dict(record=copy.deepcopy(chain), bones=points,
        weights=tuple({name: row.get(name, 0.) for name in chain['bones']}
                      for row in scope['weights'](source)),
        group_locks=tuple((name, source.vertex_groups[name].lock_weight) for name in chain['bones']),
        drivers=driver_snapshot(rig, source, paths))


def curve_author(curve):
    return dict(path=curve.data_path, index=curve.array_index, extrapolation=curve.extrapolation,
        mute=curve.mute, group=curve.group.name if curve.group else None,
        keys=tuple((tuple(key.co), tuple(key.handle_left), tuple(key.handle_right),
                    key.interpolation, key.handle_left_type, key.handle_right_type,
                    key.easing, key.amplitude, key.back, key.period) for key in curve.keyframe_points),
        modifiers=tuple((modifier.type, modifier.mute, modifier.show_expanded)
                        for modifier in curve.modifiers))


def action_author(action, slot, excluded_paths):
    if not action:
        return None
    rows = []
    if action.is_action_legacy:
        entries = [(0, 0, None, curve) for curve in action.fcurves]
    else:
        entries = [(li, si, bag.slot_handle, curve)
                   for li, layer in enumerate(action.layers) for si, strip in enumerate(layer.strips)
                   for bag in strip.channelbags for curve in bag.fcurves]
    for li, si, handle, curve in entries:
        if (slot is None or handle == slot.handle) and any(curve.data_path.startswith(path) for path in excluded_paths):
            continue
        rows.append((li, si, handle, curve_author(curve)))
    return dict(slots=tuple((item.handle, item.identifier, item.target_id_type) for item in action.slots),
                curves=rows)


def animation_author(source, selected_signatures):
    rig = source[hair.RIG_KEY]
    record = hair._read_records(source)
    selected = set(selected_signatures)
    paths = [rig.pose.bones[name].path_from_id() for chain in record['chains']
             if chain['signature'] in selected for name in chain['bones']]
    ad = rig.animation_data
    if ad is None:
        return None
    tracks = {}
    for track in ad.nla_tracks:
        strips = []
        for strip in track.strips:
            authored = action_author(strip.action, strip.action_slot, paths)
            strips.append(dict(name=strip.name, type=strip.type, action=authored,
                frame_start=strip.frame_start, frame_end=strip.frame_end,
                action_frame_start=strip.action_frame_start, action_frame_end=strip.action_frame_end,
                scale=strip.scale, repeat=strip.repeat, influence=strip.influence,
                blend_type=strip.blend_type, extrapolation=strip.extrapolation,
                mute=strip.mute, use_animated_time=strip.use_animated_time,
                use_animated_influence=strip.use_animated_influence))
        tracks[track.name] = dict(mute=track.mute, solo=track.is_solo, lock=track.lock, strips=strips)
    return dict(action=action_author(ad.action, ad.action_slot, paths),
        action_slot_handle=ad.action_slot_handle, use_nla=ad.use_nla,
        blend=ad.action_blend_type, influence=ad.action_influence,
        extrapolation=ad.action_extrapolation, tracks=tracks)


def original_authors(source, rig):
    owners = tuple(owner for owner in (rig, rig.data, source, source.data, source.data.shape_keys)
                   if owner is not None)
    return dict(source=scope['source_author'](source, rig),
                actions={action.as_pointer(): scope['curves'](action) for action in bpy.data.actions},
                drivers={owner.bl_rna.identifier + ':' + owner.name_full:
                         driver_snapshot(owner, source, ('',), rig=rig) for owner in owners},
                constraints={owner.name: [native_properties(constraint, source, rig)
                             for constraint in owner.constraints]
                             for owner in (rig, source, *rig.pose.bones)})


def assert_original(case, source, rig, before):
    current = original_authors(source, rig)
    case.assertEqual(current['source'], before['source'])
    case.assertEqual(current['drivers'], before['drivers'])
    case.assertEqual(current['constraints'], before['constraints'])
    for pointer, value in before['actions'].items():
        current = next((action for action in bpy.data.actions if action.as_pointer() == pointer), None)
        case.assertIsNotNone(current)
        case.assertEqual(scope['curves'](current), value)


def assert_animation_preserved(case, before, after):
    # Existing direct Action/slot and every original NLA strip retain all native
    # author curves outside the selected paths. A new selected-only sampling
    # track may be added, but cannot replace the existing unselected stack.
    case.assertEqual(after['action'], before['action'])
    for key in ('action_slot_handle', 'blend', 'influence', 'extrapolation'):
        case.assertEqual(after[key], before[key], key)
    for name, track in before['tracks'].items():
        case.assertIn(name, after['tracks'])
        case.assertEqual(after['tracks'][name], track)


def check_partial(case, source, signatures, count, *, activate_result=True, copy_data_animation=False):
    rig = source[hair.RIG_KEY]
    old = hair._read_records(source)
    selected = set(signatures)
    before = original_authors(source, rig)
    untouched = {chain['signature']: chain_author(source, chain['signature'])
                 for chain in old['chains'] if chain['signature'] not in selected}
    native_animation = animation_author(source, signatures)
    skin = scope['weights'](source)
    result = service.create(bpy.context, source, bone_count=count, strand_signatures=tuple(signatures),
                            frame_start=1., frame_end=8., activate=activate_result)
    assert_original(case, source, rig, before)
    output = result['mesh']
    updated = hair._read_records(output)
    old_by_signature = {chain['signature']: chain for chain in old['chains']}
    case.assertEqual({chain['signature'] for chain in updated['chains']}, set(old_by_signature))
    for chain in updated['chains']:
        signature = chain['signature']
        if signature not in selected:
            case.assertEqual(chain_author(output, signature), untouched[signature], signature)
        else:
            case.assertEqual(len(chain['bones']), count)
            case.assertEqual(chain['count'], count)
            old_names = old_by_signature[signature]['bones']
            for index, row in enumerate(scope['weights'](output)):
                old_total = sum(skin[index].get(name, 0.) for name in old_names)
                new_total = sum(row.get(name, 0.) for name in chain['bones'])
                case.assertLessEqual(abs(old_total - new_total), 2e-6)
    case.assertEqual(result['selected_signatures'], tuple(c['signature'] for c in old['chains'] if c['signature'] in selected))
    case.assertEqual(result['strand_counts'], {c['signature']: c['count'] for c in updated['chains']})
    output_mesh, original_mesh = scope['mesh_author'](output), scope['mesh_author'](source)
    if copy_data_animation:
        animation_keys = {'object_animation', 'data_animation', 'key_animation'}
        case.assertEqual({key: value for key, value in output_mesh.items() if key not in animation_keys},
                         {key: value for key, value in original_mesh.items() if key not in animation_keys})
    else:
        case.assertEqual(output_mesh, original_mesh)
    copied_animation = animation_author(output, signatures)
    if native_animation['action'] is None:
        case.assertIsNotNone(copied_animation['action'])
        case.assertEqual(copied_animation['action']['curves'], [], 'New direct Action can contain only selected-chain samples.')
        case.assertEqual((copied_animation['blend'], copied_animation['influence'], copied_animation['extrapolation']),
                         ('REPLACE', 1.0, 'HOLD'))
        for name, track in native_animation['tracks'].items():
            case.assertEqual(copied_animation['tracks'][name], track)
    else:
        assert_animation_preserved(case, native_animation, copied_animation)
    old_effective = {strand['signature']: profiles.effective(source, strand['strand_id'], registry=registry.read(source))
                     for strand in registry.read(source)['strands']}
    new_effective = {strand['signature']: profiles.effective(output, strand['strand_id'], registry=registry.read(output))
                     for strand in registry.read(output)['strands']}
    case.assertEqual(new_effective, old_effective)
    old_registry, new_registry = registry.read(source), registry.read(output)
    old_profiles = profiles.read(source, registry=old_registry)
    new_profiles = profiles.read(output, registry=new_registry)
    case.assertEqual({key: value for key, value in old_profiles.items()
                      if key not in {'source_uid', 'registry_topology', 'strands'}},
                     {key: value for key, value in new_profiles.items()
                      if key not in {'source_uid', 'registry_topology', 'strands'}})
    case.assertEqual({strand['signature']: old_profiles['strands'][strand['strand_id']]
                      for strand in old_registry['strands']},
                     {strand['signature']: new_profiles['strands'][strand['strand_id']]
                      for strand in new_registry['strands']})
    METRICS.setdefault(case._testMethodName, []).append(dict(selected=list(signatures), count=count,
        strand_counts=result['strand_counts'], untouched_exact=list(untouched),
        original_author_sha256=checksum(before['source']),
        unselected_author_sha256={sig: checksum(value) for sig, value in untouched.items()}))
    return result


class SelectedHairNative(unittest.TestCase):
    def test_single_chain_keeps_locked_unselected_native_authors(self):
        source, rig, record = selected_fixture(locked=(1, 2))
        chosen = (record['chains'][0]['signature'],)
        choose(source, chosen, every_bone=True)
        self.assertEqual(selection.selected(bpy.context, source), chosen)
        output = check_partial(self, source, chosen, 6)
        self.assertEqual(selection.selected(bpy.context, output['mesh']), chosen)

    def test_independent_symmetric_pair_changes_together_keeps_asymmetric_chain(self):
        source, rig, record = selected_fixture(locked=(2,))
        pair = tuple(chain['signature'] for chain in record['chains'][:2])
        choose(source, pair)
        self.assertEqual(selection.selected(bpy.context, source), pair)
        check_partial(self, source, pair, 7)

    def test_sequential_mixed_counts_keep_previous_strands(self):
        source, rig, record = selected_fixture(locked=(2,))
        first, second, third = [chain['signature'] for chain in record['chains']]
        current = source
        for signature, count, expected in ((first, 6, (6, 4, 4)), (second, 7, (6, 7, 4)),
                                           (first, 5, (5, 7, 4))):
            choose(current, (signature,))
            result = check_partial(self, current, (signature,), count)
            current = result['mesh']
            self.assertEqual(tuple(chain['count'] for chain in hair._read_records(current)['chains']), expected)
        from character_designer import animation_physics as physics
        self.assertIs(service.physics_owner(current), rig)
        self.assertEqual(physics._sources(bpy.context, rig, 'HAIR'), (current,))

    def test_unselected_action_nla_handles_constraints_and_drivers_are_native(self):
        source, rig, record = selected_fixture(nla=True, locked=(1, 2))
        check_partial(self, source, (record['chains'][0]['signature'],), 6)

    def test_unselected_manual_pair_proof_order_and_raw_profiles_remain(self):
        source, rig, record = selected_fixture(automatic_pairs=False)
        before_registry = registry.read(source)
        by_signature = {strand['signature']: strand for strand in before_registry['strands']}
        first, second, third = [chain['signature'] for chain in record['chains']]
        self.assertTrue(all(strand['pair_proof'] == 'NONE' for strand in before_registry['strands']))
        paired = registry.manual_pair(source, by_signature[first]['strand_id'], by_signature[second]['strand_id'])
        profiles.reconcile(source, registry=paired)
        self.assertEqual([strand['pair_proof'] for strand in paired['strands']], ['MANUAL', 'MANUAL', 'NONE'])
        def proof(data):
            names = {strand['strand_id']: strand['signature'] for strand in data['strands']}
            return {strand['signature']: dict(partner=names.get(strand['mirror_id']),
                side=strand['side'], proof=strand['pair_proof'], order=strand['order'],
                vertex_map=strand['vertex_map'], boundary_map=strand['boundary_map'])
                for strand in data['strands'] if strand['signature'] in {first, second}}
        expected = proof(paired)
        result = check_partial(self, source, (third,), 6)
        self.assertEqual(proof(registry.read(result['mesh'])), expected)

    def test_no_active_action_samples_selected_with_replace_full_hold_and_keeps_nla(self):
        source, rig, record = selected_fixture(rich=False, nla=True, locked=(1, 2))
        rig.animation_data.action = None
        rig.animation_data.action_blend_type = 'ADD'
        rig.animation_data.action_influence = .4
        rig.animation_data.action_extrapolation = 'HOLD_FORWARD'
        bpy.context.scene.frame_set(3, subframe=.25)
        bpy.context.view_layer.update()
        before = original_authors(source, rig)
        result = check_partial(self, source, (record['chains'][0]['signature'],), 6)
        self.assertIsNone(rig.animation_data.action)
        self.assertEqual(rig.animation_data.action_blend_type, 'ADD')
        self.assertAlmostEqual(rig.animation_data.action_influence, .4, places=6)
        self.assertEqual(rig.animation_data.action_extrapolation, 'HOLD_FORWARD')
        assert_original(self, source, rig, before)
        output, copied = result['mesh'], result['armature']
        new_chain = hair._read_records(output)['chains'][0]
        old_chain = record['chains'][0]
        state = service.channels.capture()
        family = service._family_pose(output)
        frame = bpy.context.scene.frame_current, bpy.context.scene.frame_subframe
        measured = []
        try:
            for time in (1, 8):
                bpy.context.scene.frame_set(time)
                bpy.context.view_layer.update()
                depsgraph = bpy.context.evaluated_depsgraph_get()
                old_eval, new_eval = rig.evaluated_get(depsgraph), copied.evaluated_get(depsgraph)
                error = max((old_eval.matrix_world @ old_eval.pose.bones[old_chain['bones'][0]].head
                             - new_eval.matrix_world @ new_eval.pose.bones[new_chain['bones'][0]].head).length,
                            (old_eval.matrix_world @ old_eval.pose.bones[old_chain['bones'][-1]].tail
                             - new_eval.matrix_world @ new_eval.pose.bones[new_chain['bones'][-1]].tail).length)
                self.assertLessEqual(error, 3e-5)
                measured.append(dict(frame=time, selected_domain_endpoint_world_error=error))
        finally:
            bpy.context.scene.frame_set(frame[0], subframe=frame[1])
            service._restore_family_pose(bpy.context, family)
            service.channels.restore(state)
            bpy.context.view_layer.update()
            service.channels.verify(state)
        METRICS[self._testMethodName].append(dict(native_endpoint_measures=measured))

    def test_mesh_and_key_second_slots_and_current_animated_values_copy_exactly(self):
        source, rig, record = selected_fixture(rich=False, locked=(1, 2))
        mesh, keys = source.data, source.data.shape_keys
        mesh['MeshArtistControl'] = .2
        def slotted(owner, id_type, path, name):
            action = bpy.data.actions.new(name)
            unused = action.slots.new(id_type=id_type, name='Unrelated first slot')
            used = action.slots.new(id_type=id_type, name='Actual author second slot')
            layer = action.layers.new('Actual data author layer')
            strip = layer.strips.new(type='KEYFRAME')
            first, second = strip.channelbags.new(unused), strip.channelbags.new(used)
            retarget._write_curve(first, '["OtherDataOwnerOnly"]', 0, [1., 8.], [.1, .9])
            retarget._write_curve(second, path, 0, [1., 8.], [.2, .4])
            ad = owner.animation_data_create()
            ad.action, ad.action_slot = action, used
            return action, used.handle, unused.handle
        mesh_action, mesh_slot, mesh_unused = slotted(mesh, 'MESH', '["MeshArtistControl"]', 'Mesh Actual Slot Action')
        key_action, key_slot, key_unused = slotted(keys, 'KEY', 'key_blocks["Artist Detail"].value', 'Key Actual Slot Action')
        family = service._family_pose(source)
        bpy.context.scene.frame_set(3, subframe=.25)
        service._restore_family_pose(bpy.context, family)
        mesh['MeshArtistControl'] = .819
        keys.key_blocks['Artist Detail'].value = .727
        bpy.context.view_layer.update()
        expected = mesh['MeshArtistControl'], keys.key_blocks['Artist Detail'].value
        snapshots = [action_author(owner.animation_data.action, owner.animation_data.action_slot, ())
                     for owner in (mesh, keys)]
        result = check_partial(self, source, (record['chains'][0]['signature'],), 6, copy_data_animation=True)
        output = result['mesh']
        for old_owner, new_owner, slot, unused, expected_action in zip(
                (mesh, keys), (output.data, output.data.shape_keys),
                (mesh_slot, key_slot), (mesh_unused, key_unused), snapshots):
            self.assertIsNot(new_owner.animation_data.action, old_owner.animation_data.action)
            self.assertEqual(new_owner.animation_data.action_slot_handle, slot)
            self.assertNotEqual(new_owner.animation_data.action_slot_handle, unused)
            self.assertEqual(action_author(new_owner.animation_data.action, new_owner.animation_data.action_slot, ()), expected_action)
            self.assertNotIn('OtherDataOwnerOnly', new_owner)
        self.assertEqual((mesh['MeshArtistControl'], keys.key_blocks['Artist Detail'].value), expected)
        self.assertEqual((output.data['MeshArtistControl'], output.data.shape_keys.key_blocks['Artist Detail'].value), expected)
        METRICS[self._testMethodName].append(dict(mesh_actual_slot=mesh_slot, key_actual_slot=key_slot,
            unused_slots=[mesh_unused, key_unused], current_author_values=expected))

    def test_key_driver_reverse_dependency_refuses_at_partial_validate_before_copy(self):
        from character_designer import hair_resegment_partial as partial
        source, rig, record = selected_fixture(rich=False)
        chosen = (record['chains'][0]['signature'],)
        key = source.data.shape_keys.key_blocks['Artist Detail']
        curve = key.driver_add('value')
        curve.driver.type = 'SCRIPTED'
        curve.driver.expression = 'spin * .1'
        variable = curve.driver.variables.new()
        variable.name, variable.type = 'spin', 'TRANSFORMS'
        target = variable.targets[0]
        target.id = rig
        target.bone_target = record['chains'][0]['bones'][0]
        target.transform_type, target.transform_space = 'ROT_X', 'LOCAL_SPACE'
        choose(source, chosen)
        bpy.context.view_layer.update()
        before = (original_authors(source, rig), scope['ids'](), scope['context_state'](), scope['visibility']())
        driver_before = (curve.data_path, curve.array_index, curve.driver.expression,
                         variable.name, variable.type, target.id, target.bone_target,
                         target.transform_type, target.transform_space)
        validate = partial.validate
        reached = []
        def checked(*positional, **keyword):
            reached.append(True)
            return validate(*positional, **keyword)
        with patch.object(partial, 'validate', side_effect=checked), patch.object(service, '_copy') as copied:
            with self.assertRaisesRegex(ValueError, 'unchanged driver depends on a selected Hair bone') as error:
                service.create(bpy.context, source, bone_count=6, strand_signatures=chosen,
                               frame_start=1., frame_end=8.)
            self.assertFalse(copied.called)
        self.assertEqual(len(reached), 1)
        self.assertEqual((original_authors(source, rig), scope['ids'](), scope['context_state'](), scope['visibility']()), before)
        self.assertEqual((curve.data_path, curve.array_index, curve.driver.expression,
                          variable.name, variable.type, target.id, target.bone_target,
                          target.transform_type, target.transform_space), driver_before)
        METRICS[self._testMethodName] = dict(partial_validate_reached=1, copy_reached=False,
                                           refusal=str(error.exception), author_and_ids_exact=True)

    def test_explicit_empty_unknown_duplicate_refuse_before_new_ids(self):
        source, rig, record = selected_fixture()
        signature = record['chains'][0]['signature']
        for chosen in ((), ('missing-strand-signature',), (signature, signature)):
            before = (scope['source_author'](source, rig), scope['ids'](), scope['context_state'](), scope['visibility']())
            with self.assertRaises((ValueError, RuntimeError)):
                service.create(bpy.context, source, bone_count=6, strand_signatures=chosen)
            self.assertEqual((scope['source_author'](source, rig), scope['ids'](), scope['context_state'](), scope['visibility']()), before)

    def test_pose_no_selection_nonhair_and_stale_dialog_pin_refuse(self):
        source, rig, record = selected_fixture()
        choose(source, ())
        before = (scope['source_author'](source, rig), scope['ids'](), scope['context_state']())
        with self.assertRaises((ValueError, RuntimeError)):
            selection.selected(bpy.context, source)
        rig.pose.bones['Arm'].select = True
        rig.data.bones.active = rig.data.bones['Arm']
        with self.assertRaises((ValueError, RuntimeError)):
            selection.selected(bpy.context, source)
        first = (record['chains'][0]['signature'],)
        choose(source, first)
        pin = json.dumps(dict(source=ui.CHARACTERDESIGNER_OT_hair_resegment._identity(source),
                              rig=ui.CHARACTERDESIGNER_OT_hair_resegment._identity(rig),
                              binding=ui.CHARACTERDESIGNER_OT_hair_resegment._fingerprint(record),
                              selected=list(first), selected_error=''))
        choose(source, (record['chains'][1]['signature'],))
        before = (scope['source_author'](source, rig), scope['ids'](), scope['context_state']())
        try:
            result = bpy.ops.character_designer.hair_resegment('EXEC_DEFAULT', scope='SELECTED', bone_count=6,
                selection_pin=pin)
        except RuntimeError as error:
            self.assertIn('changed while', str(error))
        else:
            self.assertEqual(result, {'CANCELLED'})
        self.assertEqual((scope['source_author'](source, rig), scope['ids'](), scope['context_state']()), before)

    def test_valid_bones_from_two_bound_sources_cannot_be_mixed(self):
        source, rig, record = selected_fixture(rich=False)
        other, plans = make_hair('Other Bound Hair', strands=1)
        from character_designer import hair_bones_topology as topology
        bm = bmesh.new()
        bm.from_mesh(other.data)
        for domain in (bm.verts, bm.edges, bm.faces):
            domain.ensure_lookup_table()
            domain.index_update()
        allowed, edges, adjacency = topology._visible_graph(bm)
        plans = topology._discover(other, bm, allowed, edges, adjacency, set())
        bm.free()
        activate(other)
        binding.bind_hair(bpy.context, other, plans, bone_count=4, armature=rig)
        other_record = hair._read_records(other)
        choose(source, (record['chains'][0]['signature'],))
        foreign = rig.pose.bones[other_record['chains'][0]['bones'][1]]
        foreign.select = True
        rig.data.bones.active = rig.data.bones[record['chains'][0]['bones'][0]]
        before = (scope['source_author'](source, rig), scope['source_author'](other, rig),
                  scope['ids'](), scope['context_state']())
        with self.assertRaisesRegex(ValueError, 'one bound Hair'):
            selection.selected(bpy.context, source)
        self.assertEqual((scope['source_author'](source, rig), scope['source_author'](other, rig),
                          scope['ids'](), scope['context_state']()), before)

    def test_reached_partial_weight_action_publish_faults_restore_exact_state(self):
        from character_designer import hair_resegment_partial as partial
        for owner, name in ((service, '_write_weights'), (partial, 'write_action'), (service, '_publish')):
            with self.subTest(stage=name):
                source, rig, record = selected_fixture()
                chosen = (record['chains'][0]['signature'],)
                choose(source, chosen)
                before = (original_authors(source, rig), scope['ids'](), scope['context_state'](), scope['visibility']())
                original = getattr(owner, name)
                reached = []
                def fault(*positional, **keyword):
                    original(*positional, **keyword)
                    reached.append(True)
                    raise RuntimeError('Reached selected Hair fault ' + name)
                with patch.object(owner, name, side_effect=fault):
                    with self.assertRaisesRegex((ValueError, RuntimeError), 'Reached selected Hair fault'):
                        service.create(bpy.context, source, bone_count=6, strand_signatures=chosen,
                                       frame_start=1., frame_end=8.)
                self.assertTrue(reached)
                self.assertEqual((original_authors(source, rig), scope['ids'](), scope['context_state'](), scope['visibility']()), before)

    def test_native_mirror_requires_both_sides_and_preserves_center(self):
        from test_hair_bones_mirror_controls_blender import fixture as mirror_fixture, evaluated_topology
        fresh_main()
        source, plans, rig = mirror_fixture()
        rig.data.bones['spine.006'].name = 'Head'
        binding.bind_hair(bpy.context, source, plans, bone_count=4, armature=rig)
        record = hair._read_records(source)
        registry.initialize(source)
        self.assertEqual(record['mirror_layout'], 'BEFORE_ARMATURE_X')
        by_side = {chain['mirror_side']: chain for chain in record['chains']}
        self.assertEqual(set(by_side), {'L', 'R', 'C'})
        activate(rig, 'POSE')
        before = (scope['source_author'](source, rig), scope['ids'](), scope['context_state']())
        with self.assertRaises((ValueError, RuntimeError)):
            service.create(bpy.context, source, bone_count=6,
                           strand_signatures=(by_side['L']['signature'],), frame_start=1., frame_end=8.)
        self.assertEqual((scope['source_author'](source, rig), scope['ids'](), scope['context_state']()), before)
        center = chain_author(source, by_side['C']['signature'])
        result = service.create(bpy.context, source, bone_count=6,
            strand_signatures=(by_side['L']['signature'], by_side['R']['signature']), frame_start=1., frame_end=8.)
        output, copy_rig = result['mesh'], result['armature']
        updated = {chain['mirror_side']: chain for chain in hair._read_records(output)['chains']}
        self.assertEqual(chain_author(output, by_side['C']['signature']), center)
        self.assertEqual(tuple(updated[side]['count'] for side in ('L', 'R', 'C')), (6, 6, 4))
        self.assertTrue(all(name.endswith('.L') for name in updated['L']['bones']))
        self.assertTrue(all(name.endswith('.R') for name in updated['R']['bones']))
        self.assertEqual([bpy.utils.flip_name(name) for name in updated['L']['bones']], updated['R']['bones'])
        mirror = next(modifier for modifier in output.modifiers if modifier.type == 'MIRROR')
        self.assertTrue(mirror.show_viewport and mirror.show_render and mirror.use_mirror_vertex_groups)
        self.assertEqual(evaluated_topology(output), evaluated_topology(source))
        # The native mirrored output must really be deformable by each side;
        # checking suffixes or group existence alone would miss inert controls.
        def points(obj):
            evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                return tuple(obj.matrix_world @ vertex.co for vertex in mesh.vertices)
            finally:
                evaluated.to_mesh_clear()
        baseline = points(output)
        side_indices = {side: [i for i, point in enumerate(baseline)
                              if point.x > .4] if side == 'L' else [i for i, point in enumerate(baseline) if point.x < -.4]
                        for side in ('L', 'R')}
        for side in ('L', 'R'):
            pose = copy_rig.pose.bones[updated[side]['bones'][0]]
            old = pose.rotation_quaternion.copy(), pose.rotation_mode
            pose.rotation_mode = 'XYZ'
            pose.rotation_euler.x += .25
            bpy.context.view_layer.update()
            changed = points(output)
            self.assertGreater(max((changed[i] - baseline[i]).length for i in side_indices[side]), .02)
            opposite = 'R' if side == 'L' else 'L'
            self.assertLessEqual(max((changed[i] - baseline[i]).length for i in side_indices[opposite]), 3e-5)
            pose.rotation_mode = old[1]
            pose.rotation_quaternion = old[0]
            bpy.context.view_layer.update()


result = None
registered = False
report = dict(schema='character-designer.selected-hair-native/1', passed=False,
    projection=str(args.projection), projection_sha256=args.projection_sha,
    source_package=str(PACKAGE), harness_sha256=digest(__file__),
    pinned_fixture=str(PINNED), pinned_fixture_sha256=PINNED_SHA,
    metrics=METRICS, artist_file_opened=False, scene_or_preferences_saved=False,
    autoexec_enabled=True, errors=[], cleanup_errors=[],
    limits=['Private synthetic native fixtures only; no artist/full-character/FPS acceptance.',
            'Selected changed counts are sampled approximations; unselected author data must remain native.'])
try:
    cd.register()
    registered = True
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SelectedHairNative))
except BaseException:
    report['errors'].append(traceback.format_exc())
finally:
    if registered:
        try:
            if bpy.context.object and bpy.context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            cd.unregister()
        except BaseException:
            report['cleanup_errors'].append(traceback.format_exc())
    report.update(tests_run=result.testsRun if result else 0,
        failures=[(str(case), error) for case, error in result.failures] if result else [],
        test_errors=[(str(case), error) for case, error in result.errors] if result else [],
        source_unchanged=all(digest(PACKAGE / name) == value for name, value in manifest['files'].items()))
    report['passed'] = bool(result and result.wasSuccessful() and not report['errors']
                            and not report['cleanup_errors'] and report['source_unchanged'])
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
if not report['passed']:
    raise RuntimeError('Selected-strand Hair native acceptance failed; retain this report.')
