"""Read-only real-character probe: append into a factory scene, never save.

blender --background --factory-startup --disable-autoexec --python-exit-code 1
    --python tests/verify_real_refine_symmetry_blender.py -- input.blend report.json

The source .blend's hash is verified after the in-memory repair. A refusal is
reported distinctly and must have restored the complete original coordinates.
"""
from array import array
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

import bpy
import bmesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import refine_symmetry as refine


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def packed(points):
    result = array('f', [0]) * (len(points) * 3)
    points.foreach_get('co', result)
    return result


def all_coordinates(obj):
    keys = obj.data.shape_keys
    return {'mesh': packed(obj.data.vertices),
            'keys': {key.name: packed(key.data) for key in keys.key_blocks} if keys else {}}


def deformation_delta(before, after):
    """Independently compare each key relative to Basis, including asymmetric keys."""
    old_keys, new_keys = before['keys'], after['keys']
    assert set(old_keys) == set(new_keys)
    if not old_keys:
        return 0.
    basis_name = next(iter(old_keys))
    old_basis, new_basis = old_keys[basis_name], new_keys[basis_name]
    maximum = 0.
    for name, values in old_keys.items():
        current = new_keys[name]
        for index in range(len(values)):
            error = abs((values[index] - old_basis[index]) - (current[index] - new_basis[index]))
            maximum = max(maximum, error)
    assert maximum <= 3e-6, ('Shape Key Basis delta changed', maximum)
    return maximum


def artist_digest(obj):
    """Independent protected-data snapshot; coordinates are checked separately."""
    mesh = obj.data
    digest = hashlib.sha256()

    def add(value):
        digest.update(repr(value).encode('utf-8'))
        digest.update(b'\0')

    add((obj.as_pointer(), mesh.as_pointer(), obj.name, mesh.name, len(mesh.vertices),
         tuple(vertex.index for vertex in mesh.vertices)))
    for edge in mesh.edges:
        add((edge.index, tuple(edge.vertices), edge.use_seam, edge.use_edge_sharp))
    for polygon in mesh.polygons:
        add((polygon.index, tuple(polygon.vertices), tuple(polygon.loop_indices),
             polygon.material_index, polygon.use_smooth))
    for loop in mesh.loops:
        add((loop.index, loop.vertex_index, loop.edge_index))
    for layer in mesh.uv_layers:
        add(layer.name)
        for item in layer.data:
            add(tuple(item.uv))
    for attribute in mesh.attributes:
        if attribute.name == 'position' or attribute.name.startswith(('.select', '.hide')):
            continue
        add((attribute.name, attribute.domain, attribute.data_type))
        for item in attribute.data:
            found = False
            for name in ('value', 'vector', 'color', 'uv', 'quaternion'):
                if hasattr(item, name):
                    value = getattr(item, name)
                    add(tuple(value) if not isinstance(value, str) and hasattr(value, '__len__') else value)
                    found = True
                    break
            if not found:
                raise AssertionError(f'Probe does not support attribute {attribute.name}/{attribute.data_type}')
    for group in obj.vertex_groups:
        add((group.name, group.index, group.lock_weight))
    for vertex in mesh.vertices:
        add(tuple((group.group, group.weight) for group in vertex.groups))
    add((obj.parent.as_pointer() if obj.parent else 0, obj.parent_type, obj.parent_bone,
         tuple(tuple(row) for row in obj.matrix_parent_inverse), tuple(tuple(row) for row in obj.matrix_world)))
    for modifier in obj.modifiers:
        target = getattr(modifier, 'object', None)
        add((modifier.as_pointer(), modifier.name, modifier.type, target.as_pointer() if target else 0))
    add((tuple(material.as_pointer() if material else 0 for material in mesh.materials),
         obj.active_shape_key_index, obj.show_only_shape_key,
         mesh.use_mirror_x, mesh.use_mirror_y, mesh.use_mirror_z, mesh.use_mirror_topology))
    keys = mesh.shape_keys
    if keys:
        add((keys.as_pointer(), keys.use_relative,
             keys.animation_data.action.as_pointer() if keys.animation_data and keys.animation_data.action else 0))
        for key in keys.key_blocks:
            add((key.name, key.value, key.mute, key.slider_min, key.slider_max,
                 key.relative_key.as_pointer(), key.vertex_group, key.interpolation,
                 getattr(key, 'lock_shape', False)))
    for owner in (obj, mesh, keys):
        if owner:
            add(tuple((key, repr(owner[key])) for key in owner.keys()))
    return digest.hexdigest()


def report_analysis(result):
    return {'matched_pairs': result.matched, 'misaligned_pairs_or_center_vertices': result.misaligned,
            'unmatched_vertices': len(result.unmatched), 'maximum_mirror_error': result.max_error,
            'centerline_vertices': len(result.centerline),
            'diagnostics': list(getattr(result, 'diagnostics', ())),
            'first_misaligned_vertices': list(result.misaligned_vertices[:40]),
            'first_unmatched_vertices': list(result.unmatched[:40])}


def sample_native_pairs(pairs, basis, limit=16):
    """Prioritize maximum errors and distribute the rest through vertex indices."""
    def error(pair):
        first, second = (basis[index * 3:index * 3 + 3] for index in pair)
        return math.sqrt((first[0] + second[0]) ** 2 +
                         (first[1] - second[1]) ** 2 + (first[2] - second[2]) ** 2)

    candidates = sorted((pair for pair in pairs if error(pair) > 1e-6))
    if not candidates:
        candidates = sorted(pairs)
    if len(candidates) <= limit:
        return tuple(candidates)
    chosen = sorted(candidates, key=lambda pair: (-error(pair), pair))[:4]
    slots = limit - len(chosen)
    for offset in range(slots):
        pair = candidates[round(offset * (len(candidates) - 1) / max(1, slots - 1))]
        if pair not in chosen:
            chosen.append(pair)
    # Quantiles can coincide with the four maximum-error samples.
    for pair in candidates:
        if len(chosen) == limit:
            break
        if pair not in chosen:
            chosen.append(pair)
    return tuple(chosen)


def check_native_x_mirror(obj, basis, pairs, label):
    """Run Blender's own geometric selection mirror on an expendable Mesh copy.

    Native Edit Mode may add UV selection attributes. This separate object and
    single-user Mesh prevent those attributes from affecting preservation checks
    on the real tool copy. Shape Keys are removed only from this test clone.
    """
    prior_active = bpy.context.view_layer.objects.active
    prior_selection = [(item, item.select_get()) for item in bpy.context.view_layer.objects]
    prior_select_mode = tuple(bpy.context.scene.tool_settings.mesh_select_mode)
    clone, mesh = None, None
    result = {'label': label, 'sampled_pairs': len(pairs), 'checks': 0,
              'passed': 0, 'mismatches': [], 'operator_errors': [],
              'topology_mirror_enabled': False, 'active_shape': 'Basis'}
    try:
        clone = obj.copy()
        mesh = obj.data.copy()
        clone.data = mesh
        clone.name = f'Native Mirror Probe {label}'
        bpy.context.collection.objects.link(clone)
        assert mesh is not obj.data and mesh.users == 1
        if mesh.shape_keys:
            clone.shape_key_clear()
        mesh.vertices.foreach_set('co', basis)
        mesh.update()
        clone.modifiers.clear()
        clone.parent = None
        clone.hide_viewport = False
        clone.hide_set(False)
        clone.active_shape_key_index = 0
        clone.show_only_shape_key = True
        mesh.use_mirror_topology = False
        mesh.use_mirror_x = True
        mesh.use_mirror_y = False
        mesh.use_mirror_z = False
        for sequence in (mesh.vertices, mesh.edges, mesh.polygons):
            for item in sequence:
                item.hide = False
                item.select = False
        for item, _selected in prior_selection:
            item.select_set(False)
        bpy.context.view_layer.objects.active = clone
        clone.select_set(True)
        bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)
        bpy.ops.object.mode_set(mode='EDIT')
        for negative, positive in pairs:
            for source, expected in ((negative, positive), (positive, negative)):
                result['checks'] += 1
                try:
                    bpy.ops.mesh.select_all(action='DESELECT')
                    bm = bmesh.from_edit_mesh(mesh)
                    bm.verts.ensure_lookup_table()
                    bm.verts[source].select = True
                    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
                    status = bpy.ops.mesh.select_mirror(axis={'X'}, extend=False)
                    if status != {'FINISHED'}:
                        raise RuntimeError(f'Native select_mirror returned {status}')
                    selected = sorted(vertex.index for vertex in bmesh.from_edit_mesh(mesh).verts if vertex.select)
                    if selected == [expected]:
                        result['passed'] += 1
                    else:
                        result['mismatches'].append({'source': source, 'expected': expected,
                                                     'selected': selected})
                except Exception as error:
                    result['operator_errors'].append({'source': source, 'expected': expected,
                                                       'error': f'{type(error).__name__}: {error}'})
    except Exception as error:
        result['operator_errors'].append({'stage': 'setup',
                                           'error': f'{type(error).__name__}: {error}'})
    finally:
        if clone and clone.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception as error:
                result['operator_errors'].append({'stage': 'leave_edit_mode',
                                                   'error': f'{type(error).__name__}: {error}'})
        if clone:
            bpy.data.objects.remove(clone, do_unlink=True)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        for item, selected in prior_selection:
            item.select_set(selected)
        bpy.context.view_layer.objects.active = prior_active
        bpy.context.scene.tool_settings.mesh_select_mode = prior_select_mode
    result['failed'] = result['checks'] - result['passed']
    return result


def main():
    args = sys.argv[sys.argv.index('--') + 1:]
    source, destination = Path(args[0]), Path(args[1])
    object_name = args[2] if len(args) > 2 else 'Cosha'
    before_hash = file_hash(source)
    with bpy.data.libraries.load(str(source), link=False) as (available, requested):
        assert object_name in available.objects, f'{object_name} is not in {source}'
        requested.objects = [object_name]
    obj = requested.objects[0]
    assert obj and obj.type == 'MESH'
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.context.view_layer.update()
    original, immutable = all_coordinates(obj), artist_digest(obj)
    before = refine.analyze(obj, selected_only=False)
    assert all_coordinates(obj) == original and artist_digest(obj) == immutable, 'Analyze mutated real artist data'
    result = {'source': str(source), 'source_sha256': before_hash,
              'object': object_name, 'vertex_count': len(obj.data.vertices),
              'shape_keys': list(original['keys']), 'before': report_analysis(before),
              'original_saved': False}
    success = False
    try:
        plan = refine.plan(obj, mode='AVERAGE', selected_only=False)
        validation = refine.apply(obj, plan)
        current = all_coordinates(obj)
        assert artist_digest(obj) == immutable, 'Protected artist data changed'
        max_delta_error = deformation_delta(original, current)
        unaffected = set(range(len(obj.data.vertices))) - set(plan.positions)
        for index in unaffected:
            first, last = index * 3, index * 3 + 3
            assert current['mesh'][first:last] == original['mesh'][first:last], ('Unplanned mesh coordinate', index)
            for name, values in original['keys'].items():
                assert current['keys'][name][first:last] == values[first:last], ('Unplanned key coordinate', name, index)
        assert validation.ordinary_mirror_ready
        result.update(status='VALIDATED_IN_MEMORY', after=report_analysis(validation),
                      ordinary_mirror_ready=True, changed_vertices=len(plan.positions),
                      validated_pairs=validation.validated_pairs,
                      maximum_preserved_delta_error=max_delta_error,
                      protected_artist_data_unchanged=True)
        basis_name = obj.data.shape_keys.reference_key.name if obj.data.shape_keys else None
        old_basis = original['keys'][basis_name] if basis_name else original['mesh']
        new_basis = current['keys'][basis_name] if basis_name else current['mesh']
        samples = sample_native_pairs(plan.target_pairs or before.pairs, old_basis)
        native_before = check_native_x_mirror(obj, old_basis, samples, 'Before')
        native_after = check_native_x_mirror(obj, new_basis, samples, 'After')
        result['native_x_mirror'] = {'sample_pair_indices': [list(pair) for pair in samples],
                                     'before': native_before, 'after': native_after}
        # The original appended tool copy must remain untouched by native test
        # clones, including every Shape Key, UV and stored selection attribute.
        assert all_coordinates(obj) == current and artist_digest(obj) == immutable, 'Native clone test affected the real tool copy'
        success = (native_after['checks'] == len(samples) * 2 and native_after['failed'] == 0
                   and not native_after['operator_errors'])
        if not success:
            result['status'] = 'NATIVE_MIRROR_VALIDATION_FAILED'
    except refine.RefineSymmetryError as error:
        assert all_coordinates(obj) == original and artist_digest(obj) == immutable, 'Failed repair did not fully restore real artist data'
        result.update(status='REFUSED_UNCHANGED', reason=str(error),
                      ordinary_mirror_ready=False, protected_artist_data_unchanged=True,
                      all_original_coordinates_restored=True)
    assert file_hash(source) == before_hash, 'Original saved Blender file changed'
    result['source_file_unchanged'] = True
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    marker = ('REAL_REFINE_SYMMETRY_VALIDATED' if success else
              'REAL_REFINE_SYMMETRY_REFUSED_UNCHANGED' if result['status'] == 'REFUSED_UNCHANGED' else
              'REAL_REFINE_SYMMETRY_NATIVE_CHECK_FAILED')
    print(marker, flush=True)
    return success


if __name__ == '__main__':
    try:
        sys.exit(0 if main() else 2)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
