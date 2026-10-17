"""Disposable Dress Spline IK translation diagnostics; no artist file input."""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from test_character_ground_blender import CharacterPlacementTests, activate, frustum
from character_designer import character_ground as placement, skirt_rig, body_setup


def point_error(before, after, distance=0.):
    if len(before) != len(after):
        return {'before_values': len(before), 'after_values': len(after), 'topology_changed': True}
    differences = [new - old - (distance if index % 3 == 2 else 0.)
                   for index, (old, new) in enumerate(zip(before, after))]
    largest = max(range(len(differences)), key=lambda index: abs(differences[index]), default=0)
    bbox = math.sqrt(sum((max(before[axis::3]) - min(before[axis::3])) ** 2 for axis in range(3))) if before else 0.
    rms = math.sqrt(sum(d * d for d in differences) / max(len(differences), 1))
    return {'vertices': len(before) // 3, 'max_abs': max(map(abs, differences), default=0.),
            'rms_components': rms, 'bbox_diagonal': bbox,
            'rms_over_bbox': rms / max(bbox, 1e-20), 'worst_vertex': largest // 3,
            'worst_axis': 'XYZ'[largest % 3], 'worst_signed': differences[largest] if differences else 0.}


def curve_snapshot(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        local = [value for vertex in mesh.vertices for value in vertex.co]
        world = [value for vertex in mesh.vertices for value in (evaluated.matrix_world @ vertex.co)]
        local_length = sum((mesh.vertices[e.vertices[0]].co - mesh.vertices[e.vertices[1]].co).length for e in mesh.edges)
        world_length = sum((evaluated.matrix_world @ mesh.vertices[e.vertices[0]].co
                            - evaluated.matrix_world @ mesh.vertices[e.vertices[1]].co).length for e in mesh.edges)
        return {'local': local, 'world': world, 'local_length': local_length, 'world_length': world_length}
    finally:
        evaluated.to_mesh_clear()


def snapshot(state):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    result = {'meshes': {obj.name: placement._points(obj, depsgraph) for obj in state['points']},
              'bones': {}, 'curves': {}, 'objects': {}}
    for obj in state['objects']:
        evaluated = obj.evaluated_get(depsgraph)
        result['objects'][obj.name] = evaluated.matrix_world.copy()
        if obj.type == 'ARMATURE':
            for pb in evaluated.pose.bones:
                result['bones'][obj.name + '/' + pb.name] = {
                    'world': evaluated.matrix_world @ pb.matrix,
                    'local': pb.matrix.copy(), 'basis': pb.matrix_basis.copy()}
        elif obj.type == 'CURVE':
            result['curves'][obj.name] = curve_snapshot(obj, depsgraph)
    return result


def matrix_error(a, b):
    return max(abs(a[row][column] - b[row][column]) for row in range(4) for column in range(4))


def compare(before, after, distance):
    inverse_translation = Matrix.Translation((0., 0., -distance))
    bones = []
    for name, old in before['bones'].items():
        new = after['bones'][name]
        bones.append({'name': name,
                      'world_max_abs': matrix_error(old['world'], inverse_translation @ new['world']),
                      'local_max_abs': matrix_error(old['local'], new['local']),
                      'basis_max_abs': matrix_error(old['basis'], new['basis'])})
    curves = []
    for name, old in before['curves'].items():
        new = after['curves'][name]
        curves.append({'name': name, 'local': point_error(old['local'], new['local']),
                       'world': point_error(old['world'], new['world'], distance),
                       'local_length_delta': new['local_length'] - old['local_length'],
                       'world_length_delta': new['world_length'] - old['world_length']})
    return {'meshes': {name: point_error(old, after['meshes'][name], distance)
                       for name, old in before['meshes'].items()},
            'bones_top20': sorted(bones, key=lambda item: item['world_max_abs'], reverse=True)[:20],
            'curves': sorted(curves, key=lambda item: item['local'].get('max_abs', 0.), reverse=True),
            'object_max_abs': max((matrix_error(old, inverse_translation @ after['objects'][name])
                                   for name, old in before['objects'].items()), default=0.)}


def main(output, bend):
    CharacterPlacementTests.setUpClass()
    fixture = CharacterPlacementTests()
    fixture.setUp()
    rig = fixture.rig
    dress = frustum('Attached dress', rows=6, sides=16)
    dress_record = skirt_rig.build_skirt(bpy.context, dress, armature=rig, shared=False)
    if bend:
        dress[skirt_rig.RIG_KEY].pose.bones[dress_record['controls']['mid']].location = (bend, bend * .5, 0.)
    activate(rig)
    bpy.context.view_layer.update()
    state = placement._prepare(bpy.context, rig)
    original_points = {obj.name: values for obj, values in state['points'].items()}
    before = snapshot(state)
    report = {'blender': bpy.app.version_string, 'mid_control_bend': bend,
              'scope': sorted(obj.name for obj in state['objects']),
              'roots': [obj.name for obj in state['roots']], 'stages': [],
              'initial_snapshot_vs_prepare': {name: point_error(original_points[name], values)
                                              for name, values in before['meshes'].items()}}
    with body_setup._runtime_paused():
        # All writes below are to disposable fixture root locations only.
        for distance in (-.2, -.25):
            placement._restore_roots(state)
            placement._tag_scope(state['objects'])
            bpy.context.view_layer.update()
            restored = snapshot(state)
            report['stages'].append({'stage': 'restored_before_' + str(distance),
                                     'comparison': compare(before, restored, 0.)})
            delta = Vector((0., 0., distance))
            for root in state['roots']:
                offset = root.matrix_basis.to_3x3() @ state['world'][root].to_3x3().inverted() @ delta
                root.location = Vector(state['channels'][root]['channels']['location']) + offset
            for mode in ('tag_update_1', 'update_only_2', 'depsgraph_update_3', 'tag_update_4', 'tag_update_5'):
                if mode.startswith('tag_'):
                    placement._tag_scope(state['objects'])
                if mode.startswith('depsgraph_'):
                    bpy.context.evaluated_depsgraph_get().update()
                bpy.context.view_layer.update()
                after = snapshot(state)
                comparison = compare(before, after, distance)
                comparison['meshes_vs_restored'] = {name: point_error(values, after['meshes'][name], distance)
                                                     for name, values in restored['meshes'].items()}
                report['stages'].append({'stage': mode, 'distance': distance, 'comparison': comparison})
        placement._restore_roots(state)
        placement._tag_scope(state['objects'])
        bpy.context.view_layer.update()
    Path(output).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('GROUND_DRESS_PROBE_WRITTEN', output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--bend', type=float, default=0.)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    main(args.output, args.bend)
