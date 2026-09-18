"""Diagnose real skin differences on disposable source/mesh copies only."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix
from mathutils.kdtree import KDTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
import character_designer
from character_designer import unity_animation as ua, forearm_twist as ft
from validate_unity_animation_character import positions, vector, source_fingerprint


def correspondence(coords, expected):
    kd = KDTree(len(coords))
    for i, co in enumerate(coords):
        kd.insert(co, i)
    kd.balance()
    matches = [kd.find(co) for co in expected]
    return [m[1] for m in matches], [m[2] for m in matches]


def statistics(values):
    values = sorted(values)
    return {'maximum_m': max(values, default=0),
            'rms_m': math.sqrt(sum(v*v for v in values) / max(1, len(values))),
            'p99_m': values[min(len(values)-1, int(len(values)*.99))] if values else 0,
            'over_0_1mm': sum(v > .0001 for v in values), 'count': len(values)}


def clone_variant(obj, suffix, *, linear=False, top4=False, skin_last=False, bake_geometry=False):
    clone = obj.copy()
    clone.data = obj.data.copy()
    clone.name = obj.name + '__' + suffix
    bpy.context.scene.collection.objects.link(clone)
    clone.pop(ft.RECORD_KEY, None)
    if clone.data.shape_keys:
        for key in clone.data.shape_keys.key_blocks:
            if key.name.startswith(ft.KEY_PREFIX):
                key.mute = True
                key.value = 0
    for mod in clone.modifiers:
        if mod.type == 'ARMATURE' and linear:
            mod.use_deform_preserve_volume = False
    if bake_geometry:
        skin = next(m for m in clone.modifiers if m.type == 'ARMATURE')
        armature = skin.object
        skin.show_viewport = False
        bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(clone.evaluated_get(graph), preserve_all_data_layers=True, depsgraph=graph)
        clone.modifiers.clear()
        clone.data = mesh
        skin = clone.modifiers.new('Diagnostic Linear Skin', 'ARMATURE')
        skin.object = armature
    if top4:
        deform = {g.index for g in clone.vertex_groups if g.name in {b.name for m in clone.modifiers if m.type == 'ARMATURE' and m.object for b in m.object.data.bones if b.use_deform}}
        for vertex in clone.data.vertices:
            weights = sorted(((g.group, g.weight) for g in vertex.groups if g.group in deform), key=lambda item: item[1], reverse=True)
            for group, _ in weights[4:]:
                clone.vertex_groups[group].remove([vertex.index])
    if skin_last:
        skin = next((m for m in clone.modifiers if m.type == 'ARMATURE'), None)
        if skin:
            with bpy.context.temp_override(object=clone, active_object=clone):
                while list(clone.modifiers).index(skin) < len(clone.modifiers)-1:
                    bpy.ops.object.modifier_move_down(modifier=skin.name)
    return clone


def sample(scene, time, fps):
    ua._set_frame(scene, 1 + time * fps)
    bpy.context.view_layer.update()
    ft.update_runtime(scene, bpy.context.evaluated_depsgraph_get())
    bpy.context.view_layer.update()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--blend', required=True)
    parser.add_argument('--motion', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--legacy-calibration-harness', action='store_true',
                        help='For old export fixtures only: isolate native rest bones in this disposable process, preserving mesh weights and geometry.')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    source_hash = hashlib.sha256(Path(args.blend).read_bytes()).hexdigest()
    bpy.ops.wm.open_mainfile(filepath=args.blend, load_ui=False, use_scripts=False)
    character_designer.register()
    scene, rig = bpy.context.scene, bpy.data.objects['CoshaRig']
    data = ua.load_package(args.motion)
    legacy_harness = None
    if args.legacy_calibration_harness:
        names = {bone['name'] for bone in data['bones']} & set(rig.data.bones.keys())
        legacy_harness = {'removed_constraint_count':sum(len(pb.constraints) for pb in rig.pose.bones),
                          'note':'Diagnostic old-export harness only; generated bones/constraints are isolated in memory. This is not an importer compatibility claim for Body Setup.'}
        old_rest = {name:rig.data.bones[name].matrix_local.copy() for name in names}
        for pb in rig.pose.bones:
            for constraint in list(pb.constraints):
                pb.constraints.remove(constraint)
        rig.animation_data_clear()
        rig.data.animation_data_clear()
        bpy.context.view_layer.objects.active = rig
        rig.hide_set(False)
        rig.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        for bone in list(rig.data.edit_bones):
            if bone.name not in names:
                rig.data.edit_bones.remove(bone)
        for bone in data['bones']:
            if bone['name'] not in names:
                continue
            edit = rig.data.edit_bones[bone['name']]
            parent = data['bones'][bone['parent']]['name'] if bone['parent'] >= 0 else None
            edit.use_connect = False
            edit.parent = rig.data.edit_bones.get(parent) if parent else None
        bpy.ops.object.mode_set(mode='OBJECT')
        for owner in [rig, rig.data, *rig.data.bones, *rig.pose.bones]:
            for key in tuple(owner.keys()):
                if key.startswith('character_designer_'):
                    del owner[key]
        legacy_harness['rest_matrix_max_difference'] = max(abs(old_rest[n][r][c]-rig.data.bones[n].matrix_local[r][c]) for n in names for r in range(4) for c in range(4))
        assert legacy_harness['rest_matrix_max_difference'] < 1e-6
    mapping = ua._mapping(rig, data, scene.unit_settings.scale_length)
    definitions = {d['name']: d for d in data['meshes']}
    originals = [bpy.data.objects[name] for name in definitions]
    fingerprint = source_fingerprint(rig, originals)
    snapshot = ua._snapshot(bpy.context, rig)
    previous = rig.animation_data.action if rig.animation_data else None
    if rig.animation_data:
        rig.animation_data.action = None
        rig.animation_data.use_nla = False
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    ft.update_runtime(scene, bpy.context.evaluated_depsgraph_get())
    bpy.context.view_layer.update()
    variants, indices, report = {}, {}, {'source': args.blend, 'motion': args.motion, 'meshes': {}, 'forearm': [],
                                        'legacy_calibration_harness':legacy_harness}
    for obj in originals:
        names = {g.index:g.name for g in obj.vertex_groups}
        deform = {b.name for b in rig.data.bones if b.use_deform}
        influences = [sum(g.weight > 0 and names[g.group] in deform for g in v.groups) for v in obj.data.vertices]
        report['meshes'][obj.name] = {
            'source_vertices': len(obj.data.vertices), 'source_topology': ft._topology(obj.data),
            'unity_vertices': definitions[obj.name]['vertexCount'],
            'maximum_deform_influences': max(influences), 'vertices_over_4_influences': sum(n>4 for n in influences),
            'unweighted_vertices': [i for i,n in enumerate(influences) if n == 0],
            'modifiers': [{'type':m.type, 'name':m.name, 'enabled':m.show_viewport,
                           'preserve_volume':getattr(m,'use_deform_preserve_volume',None),
                           'levels':getattr(m,'levels',None)} for m in obj.modifiers],
            'artist_shapes': [(k.name,k.value,k.mute) for k in obj.data.shape_keys.key_blocks if not k.name.startswith(ft.KEY_PREFIX)] if obj.data.shape_keys else [],
            'variants': {}}
        candidates = {'source_corrected': obj,
                      'source_uncorrected': clone_variant(obj,'uncorrected'),
                      'linear_all': clone_variant(obj,'linear',linear=True),
                      'linear_top4': clone_variant(obj,'linear4',linear=True,top4=True),
                      'subdivision_before_linear_all': clone_variant(obj,'skin_last',linear=True,skin_last=True),
                      'subdivision_before_linear_top4': clone_variant(obj,'skin_last4',linear=True,top4=True,skin_last=True),
                      'baked_subdivision_linear_all': clone_variant(obj,'baked_all',linear=True,bake_geometry=True),
                      'baked_subdivision_linear_top4': clone_variant(obj,'baked_four',linear=True,top4=True,bake_geometry=True)}
        variants[obj.name] = candidates
        expected = [mapping.conversion @ vector(p) for p in definitions[obj.name]['restPositions']]
        bpy.context.view_layer.update()
        for label, candidate in candidates.items():
            coords = positions(candidate)
            matches, distances = correspondence(coords, expected)
            indices[obj.name,label] = matches
            report['meshes'][obj.name]['variants'][label] = {'rest': statistics(distances), 'samples': []}
    ua._restore_snapshot(bpy.context, rig, snapshot, previous)
    ua.import_test_action(bpy.context, rig, args.motion)
    fps = scene.render.fps / scene.render.fps_base
    saved = []
    for frame in data['frames']:
        if not frame.get('meshes'):
            continue
        sample(scene, frame['time'], fps)
        print('SKIN_SAMPLE', frame['time'], flush=True)
        for mesh in frame['meshes']:
            name = mesh['name']
            expected_corrected = [mapping.conversion @ vector(v) for v in mesh['positions']]
            expected_uncorrected = [mapping.conversion @ vector(v) for v in mesh['uncorrectedPositions']]
            coordinates = {}
            for label, obj in variants[name].items():
                coords = positions(obj)
                coordinates[label] = coords
                expected = expected_corrected if label == 'source_corrected' else expected_uncorrected
                errors = [(coords[match]-point).length for match, point in zip(indices[name,label],expected)]
                entry = {'time':frame['time'], **statistics(errors)}
                entry['worst_unity_vertex'] = max(range(len(errors)), key=errors.__getitem__)
                if label == 'baked_subdivision_linear_top4':
                    worst = entry['worst_unity_vertex']
                    dest = indices[name,label][worst]
                    entry['worst_vertex_details'] = {
                        'blender_vertex':dest, 'blender_world':list(coords[dest]), 'unity_world':list(expected[worst]),
                        'weights':[(obj.vertex_groups[g.group].name,g.weight) for g in obj.data.vertices[dest].groups],
                        'unweighted_error': statistics([error for error,match in zip(errors,indices[name,label])
                                                       if not any(g.weight>0 for g in obj.data.vertices[match].groups)]),
                        'weighted_error': statistics([error for error,match in zip(errors,indices[name,label])
                                                     if any(g.weight>0 for g in obj.data.vertices[match].groups)])}
                report['meshes'][name]['variants'][label]['samples'].append(entry)
            saved.append((frame['time'], name, coordinates['source_corrected']))
            if name == 'Cosha':
                source_delta = [a-b for a,b in zip(coordinates['source_corrected'],coordinates['source_uncorrected'])]
                unity_delta = [mapping.conversion.to_3x3() @ vector(v) for v in mesh['correctionDelta']]
                errors = [(source_delta[match]-delta).length for match,delta in zip(indices[name,'source_corrected'],unity_delta)]
                active = [i for i,v in enumerate(unity_delta) if v.length > 1e-6]
                report['forearm'].append({'time':frame['time'], 'source_active_vertices':sum(v.length>1e-6 for v in source_delta),
                    'unity_active_vertices':len(active), 'unity_delta_max_m':max((v.length for v in unity_delta),default=0),
                    'delta_error':statistics(errors), 'active_delta_error':statistics([errors[i] for i in active]),
                    'runtime_errors':dict(ft._ERRORS)})
    repeat = 0
    for time,name,original in reversed(saved):
        sample(scene,time,fps)
        actual = positions(bpy.data.objects[name])
        repeat = max(repeat,max((a-b).length for a,b in zip(actual,original)))
    sample(scene, data['duration'] * .5, fps)
    root_before = rig.matrix_world.copy()
    root_rotation = Matrix.Rotation(.71, 4, 'Z')
    root_positions = {name: positions(items['source_corrected']) for name, items in variants.items()}
    root_deltas = {name: [a-b for a,b in zip(root_positions[name], positions(items['source_uncorrected']))]
                   for name,items in variants.items()}
    rig.matrix_world = root_rotation @ root_before
    bpy.context.view_layer.update()
    ft.update_runtime(scene, bpy.context.evaluated_depsgraph_get())
    bpy.context.view_layer.update()
    report['root_rotation'] = {}
    report['root_rotation_operation'] = 'Armature Object only; unparented accessories retain their object space. Kept separate from the pose-root test below.'
    for name,items in variants.items():
        actual = positions(items['source_corrected'])
        uncorrected = positions(items['source_uncorrected'])
        report['root_rotation'][name] = {
            'skin_covariance_error': statistics([(a-root_rotation@b).length for a,b in zip(actual,root_positions[name])]),
            'correction_covariance_error': statistics([((a-b)-root_rotation.to_3x3()@delta).length
                                                     for a,b,delta in zip(actual,uncorrected,root_deltas[name])])}
    rig.matrix_world = root_before
    bpy.context.view_layer.update()
    sample(scene, data['duration'] * .5, fps)
    root_positions = {name: positions(items['source_corrected']) for name, items in variants.items()}
    root_deltas = {name: [a-b for a,b in zip(root_positions[name], positions(items['source_uncorrected']))]
                   for name,items in variants.items()}
    for pb in rig.pose.bones:
        if pb.parent is None:
            pb.matrix = rig.matrix_world.inverted() @ root_rotation @ rig.matrix_world @ pb.matrix
    bpy.context.view_layer.update()
    ft.update_runtime(scene, bpy.context.evaluated_depsgraph_get())
    bpy.context.view_layer.update()
    report['root_bone_rotation'] = {}
    for name,items in variants.items():
        actual = positions(items['source_corrected'])
        uncorrected = positions(items['source_uncorrected'])
        report['root_bone_rotation'][name] = {
            'skin_covariance_error': statistics([(a-root_rotation@b).length for a,b in zip(actual,root_positions[name])]),
            'correction_covariance_error': statistics([((a-b)-root_rotation.to_3x3()@delta).length
                                                     for a,b,delta in zip(actual,uncorrected,root_deltas[name])])}
    ua.restore_preview(bpy.context,rig)
    report['reverse_seek_skin_error_m'] = repeat
    report['source_geometry_weights_rest_artist_shapes_unchanged'] = source_fingerprint(rig,originals)==fingerprint
    report['source_file_unchanged'] = hashlib.sha256(Path(args.blend).read_bytes()).hexdigest()==source_hash
    report['note'] = 'Copies only: variants isolate preserve-volume, influence counts, and subdivision order; no fix was applied to the input.'
    Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf8')
    print('SKIN_REPORT',args.report,flush=True)


if __name__ == '__main__':
    main()
