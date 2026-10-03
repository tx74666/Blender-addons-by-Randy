"""Reproduce an exported live selection on expendable JSON-built Mesh copies.

Only reads the explicit JSON snapshot, never loads/saves/changes an artist .blend.
Native G and Select Mirror checks use each repaired copy's same Edit BMesh.
"""
from array import array
import hashlib
import json
from pathlib import Path
import sys
import traceback

import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
from character_designer import native_symmetry_pairs as native
from character_designer import refine_symmetry as refine
from character_designer import symmetry_pairs as previous
from test_refine_symmetry_blender import coordinates, key_coordinates, reset


def mirror(coordinate):
    return (-coordinate[0], coordinate[1], coordinate[2])


def error(first, second):
    return (Vector(first) - Vector(mirror(second))).length


def digest(obj):
    mesh = obj.data
    return (
        obj.as_pointer(), mesh.as_pointer(),
        tuple((v.index, tuple((g.group, g.weight) for g in v.groups)) for v in mesh.vertices),
        tuple(tuple(e.vertices) for e in mesh.edges),
        tuple(tuple(f.vertices) for f in mesh.polygons),
        tuple((l.vertex_index, l.edge_index) for l in mesh.loops),
        tuple((layer.name, tuple(tuple(item.uv) for item in layer.data)) for layer in mesh.uv_layers),
        tuple((g.name, g.index) for g in obj.vertex_groups),
        tuple((key.name, key.relative_key.name, key.value) for key in mesh.shape_keys.key_blocks),
        obj.active_shape_key_index,
    )


def build(snapshot):
    reset()
    mesh = bpy.data.meshes.new('Exported Cosha Native Mirror Probe')
    mesh.from_pydata(snapshot['coordinates'], snapshot['edges'], snapshot['faces'])
    mesh.update()
    obj = bpy.data.objects.new('Exported Cosha Native Mirror Probe', mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    for record in snapshot['keys']:
        key = obj.shape_key_add(name=record['name'])
        key.data.foreach_set('co', array('f', (value for point in record['coordinates'] for value in point)))
    obj.active_shape_key_index = 0
    uv = mesh.uv_layers.new(name='Probe UV preservation sentinel')
    for index, item in enumerate(uv.data):
        item.uv = (index / 13001., index / 17311.)
    weights = obj.vertex_groups.new(name='Probe weight preservation sentinel')
    for index in range(len(mesh.vertices)):
        weights.add([index], (index % 97 + 1) / 100., 'REPLACE')
    for collection in (mesh.vertices, mesh.edges, mesh.polygons):
        for item in collection:
            item.select = False
    for index in snapshot['selection']:
        mesh.vertices[index].select = True
    mesh.use_mirror_x = True
    mesh.use_mirror_y = False
    mesh.use_mirror_z = False
    mesh.use_mirror_topology = False
    bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)
    bpy.context.scene.tool_settings.use_mesh_automerge = False
    bpy.context.scene.tool_settings.use_proportional_edit = False
    bpy.context.view_layer.update()
    # Blender itself adds UV selection attributes on the first Edit round-trip.
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.object.mode_set(mode='OBJECT')
    for collection in (mesh.vertices, mesh.edges, mesh.polygons):
        for item in collection:
            item.select = False
    for index in snapshot['selection']:
        mesh.vertices[index].select = True
    return obj


def select_live(obj, indices):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bpy.ops.mesh.select_all(action='DESELECT')
    bm.select_history.clear()
    for index in indices:
        bm.verts[index].select_set(True)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return bm


def native_mirror_selection(obj, source, *, topology):
    obj.data.use_mirror_topology = topology
    bm = select_live(obj, [source])
    status = bpy.ops.mesh.select_mirror(axis={'X'}, extend=False)
    assert status == {'FINISHED'}
    return sorted(v.index for v in bm.verts if v.select)


def pair_map(pairs):
    return {index: other for first, second in pairs
            for index, other in ((first, second), (second, first))}


def max_delta_error(before, after):
    old_basis, new_basis = before[0][1], after[0][1]
    maximum = 0.
    for (_name, old_points), (_current_name, current_points) in zip(before, after):
        for index, (old, current) in enumerate(zip(old_points, current_points)):
            maximum = max(maximum, *(abs((old[dimension] - old_basis[index][dimension]) -
                                        (current[dimension] - new_basis[index][dimension]))
                                     for dimension in range(3)))
    return maximum


def run_mode(snapshot, mode):
    obj = build(snapshot)
    before_coordinates, before_keys, immutable = coordinates(obj), key_coordinates(obj), digest(obj)
    before_mesh = tuple(tuple(vertex.co) for vertex in obj.data.vertices)
    result = {'mode': mode, 'selected_only': True, 'selected_vertices': snapshot['selection']}
    bpy.ops.object.mode_set(mode='EDIT')
    select_live(obj, snapshot['selection'])
    try:
        plan = refine.plan(obj, mode=mode, selected_only=True)
        result['planned_pairs'] = [list(pair) for pair in plan.target_pairs]
        result['planned_vertices'] = sorted(plan.positions)
        assert set(plan.positions) <= set(snapshot['selection'])
        validation = refine.apply(obj, plan)
        assert obj.mode == 'EDIT'
        assert validation.ordinary_mirror_ready
        current_keys = key_coordinates(obj)
        current = coordinates(obj)
        current_mesh = tuple(tuple(vertex.co) for vertex in obj.data.vertices)
        unaffected = set(range(len(current))) - set(plan.positions)
        for index in unaffected:
            assert current[index] == before_coordinates[index], ('Unselected Basis write', mode, index)
            assert current_mesh[index] == before_mesh[index], ('Unselected Mesh write', mode, index)
            for (_name, points), (_old_name, old_points) in zip(current_keys, before_keys):
                assert points[index] == old_points[index], ('Unselected Shape Key write', mode, _name, index)
        maximum_delta = max_delta_error(before_keys, current_keys)
        assert maximum_delta <= 3e-7, maximum_delta
        result.update(status='REPAIRED', changed_vertices=len(plan.positions),
                      maximum_pair_error=validation.max_error,
                      maximum_shape_key_delta_error=maximum_delta,
                      all_unaffected_coordinates_unchanged=True,
                      unaffected_vertices=len(unaffected),
                      basis_after={str(index): current[index] for index in snapshot['selection']})
        # Native queries happen directly after repair, before leaving Edit Mode.
        checks = []
        for first, second in plan.target_pairs:
            for source, target in ((first, second), (second, first)):
                selected = native_mirror_selection(obj, source, topology=False)
                assert selected == [target], ('Native geometry Select Mirror', mode, source, target, selected)
                checks.append({'source': source, 'expected': target, 'selected': selected})
        result['ordinary_native_select_mirror_same_edit'] = checks
        # Native G must move only each selected source and its mirrored target.
        transform_checks = []
        for first, second in plan.target_pairs:
            for source, target in ((first, second), (second, first)):
                bm = select_live(obj, [source])
                saved = tuple(tuple(v.co) for v in bm.verts)
                movement = (.000113, 0., 0.)
                status = bpy.ops.transform.translate(value=movement, mirror=True, use_proportional_edit=False)
                assert status == {'FINISHED'}
                actual_source = tuple(bm.verts[source].co)
                actual_target = tuple(bm.verts[target].co)
                assert (Vector(actual_source) - Vector(saved[source]) - Vector(movement)).length <= 2e-7
                assert (Vector(actual_target) - Vector(saved[target]) - Vector(mirror(movement))).length <= 2e-7
                for index, coordinate in enumerate(saved):
                    if index not in (source, target):
                        assert tuple(bm.verts[index].co) == coordinate, ('Native G changed unrelated vertex', index)
                transform_checks.append({'source': source, 'target': target,
                                         'source_movement': list(Vector(actual_source) - Vector(saved[source])),
                                         'target_movement': list(Vector(actual_target) - Vector(saved[target])),
                                         'passed': True})
                # Rewind only this expendable verification move, not the repair.
                for index, coordinate in enumerate(saved):
                    bm.verts[index].co = coordinate
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        result['ordinary_native_g_same_edit'] = transform_checks
        assert len(checks) == 2 * len(plan.target_pairs) and checks
        assert len(transform_checks) == len(checks)
        bpy.ops.object.mode_set(mode='OBJECT')
        assert digest(obj) == immutable, 'Repair changed protected index/topology/UV/weight/key metadata'
        assert coordinates(obj) == current and key_coordinates(obj) == current_keys, 'Temporary native G verification was not fully rewound'
        assert tuple(tuple(vertex.co) for vertex in obj.data.vertices) == current_mesh
        result['protected_topology_uv_weights_unchanged'] = True
        result['topology_mirror_disabled'] = not obj.data.use_mirror_topology
    except refine.RefineSymmetryError as failure:
        if obj.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
        assert coordinates(obj) == before_coordinates and key_coordinates(obj) == before_keys
        assert digest(obj) == immutable
        result.update(status='REFUSED_UNCHANGED', reason=str(failure), no_geometric_pair_guess=True)
    finally:
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    return result


def main():
    args = sys.argv[sys.argv.index('--') + 1:]
    source, destination = Path(args[0]), Path(args[1])
    contents = source.read_bytes()
    source_hash = hashlib.sha256(contents).hexdigest()
    snapshot = json.loads(contents)
    points, links, faces = snapshot['coordinates'], snapshot['edges'], snapshot['faces']
    selected = snapshot['selection']
    lookup = native.native_vertex_lookup(points, links, faces)
    prior = previous.build_vertex_pairs(points, links, faces)
    prior_pairs = pair_map(prior.pairs)
    native_pairs = native.build_vertex_pairs(points, links, faces)
    counterparts = []
    for index in selected:
        target = lookup[index]
        counterparts.append({'index': index, 'coordinate': points[index],
                             'old_custom_target': prior_pairs.get(index),
                             'native_target': target,
                             'native_coordinate': points[target] if target >= 0 else None,
                             'native_mirror_error': error(points[index], points[target]) if target >= 0 else None})
    obj = build(snapshot)
    bpy.ops.object.mode_set(mode='EDIT')
    native_before = [{'source': index,
                      'topology_mirror_selected': native_mirror_selection(obj, index, topology=True),
                      'ordinary_mirror_selected': native_mirror_selection(obj, index, topology=False)}
                     for index in selected]
    bpy.ops.object.mode_set(mode='OBJECT')
    result = {
        'snapshot_source': str(source), 'snapshot_sha256': source_hash,
        'vertex_count': len(points), 'edge_count': len(links), 'face_count': len(faces),
        'shape_keys': [key['name'] for key in snapshot['keys']],
        'selected_vertices': selected,
        'selected_pair_mirror_error': error(points[selected[0]], points[selected[1]]),
        'selected_are_each_others_native_pair': lookup[selected[0]] == selected[1] and lookup[selected[1]] == selected[0],
        'counterparts': counterparts,
        'old_custom_pairs': len(prior.pairs), 'native_cross_plane_pairs': len(native_pairs.pairs),
        'native_unmatched_vertices': len(native_pairs.unmatched),
        'native_before': native_before,
        'modes': [run_mode(snapshot, mode)
                  for mode in ('LEFT_TO_RIGHT', 'RIGHT_TO_LEFT', 'AVERAGE')],
        'artist_blend_loaded_or_saved': False,
        'snapshot_file_unchanged': source.read_bytes() == contents,
    }
    assert result['snapshot_file_unchanged']
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    print('LIVE_SELECTION_NATIVE_REPRODUCED', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
