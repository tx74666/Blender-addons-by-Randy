"""Actual weapon display from authenticated captured Unity mesh geometry.

Only this module's owned display objects are added/removed. No hand solver,
global weapon asset, artist Rest, source Action or grip calibration is changed.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

from . import animation_composition_contract as contract
from .animation_composition_controls import _host, _require
from .animation_composition_weapon_geometry import compare_surface_points

SCHEMA = 'cdesigner.weapon-context/1'
RECORD = 'character_designer_composition_weapon_v1'
OWNER = 'character_designer_composition_weapon_owner'


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _vector(value):
    _require(isinstance(value, dict) and set(value) == {'x', 'y', 'z'}, 'Invalid captured weapon vertex.')
    result = tuple(value[key] for key in ('x', 'y', 'z'))
    _require(all(type(v) in (int, float) and math.isfinite(v) for v in result),
             'Non-finite captured weapon geometry.')
    return result


def _matrix(values):
    from mathutils import Matrix
    _require(isinstance(values, list) and len(values) == 16 and all(
        type(v) in (int, float) and math.isfinite(v) for v in values), 'Invalid weapon context matrix.')
    result = Matrix([values[index:index + 4] for index in range(0, 16, 4)])
    _require(max(abs(result[3][i]) for i in range(3)) <= 2e-5 and abs(result[3][3] - 1) <= 2e-5
             and abs(result.determinant()) > 1e-12, 'Weapon context transform is not affine and nonsingular.')
    return result


def _project_source(document, asset_path):
    model = Path(document['modelFile']).resolve()
    suffix = Path(document['modelAssetPath'])
    _require(not suffix.is_absolute() and suffix.parts and suffix.parts[0] == 'Assets'
             and '..' not in suffix.parts, 'Weapon source model is outside the captured Unity project.')
    root = model.parents[len(suffix.parts) - 1]
    _require((root / suffix).resolve() == model, 'Captured model path is inconsistent.')
    relative = Path(asset_path)
    _require(not relative.is_absolute() and relative.parts and relative.parts[0] == 'Assets'
             and '..' not in relative.parts, 'Invalid captured weapon asset path.')
    candidate = (root / relative).resolve()
    _require(candidate.is_relative_to(root / 'Assets'), 'Captured weapon asset escapes Unity Assets.')
    return candidate


def _source_dependencies(document):
    sources, excluded = document.get('sourceDependencies'), document.get('excludedDependencies')
    _require(isinstance(sources, list) and sources and isinstance(excluded, list)
             and len(sources) + len(excluded) <= 4096, 'Captured weapon source dependency evidence is incomplete.')
    records, names = {}, []
    for entry in sources:
        _require(isinstance(entry, dict) and set(entry) == {'assetPath', 'file', 'guid', 'fileSha256', 'metaSha256'},
                 'Invalid captured weapon dependency record.')
        asset = entry['assetPath']
        _require(isinstance(asset, str) and asset.startswith(('Assets/', 'Packages/'))
                 and '\\' not in asset and all(part not in {'', '.', '..'} for part in asset.split('/')),
                 'Invalid captured weapon dependency asset path.')
        _require(isinstance(entry['file'], str) and entry['file'], 'Invalid captured weapon dependency file path.')
        file = Path(entry['file'])
        _require(file.is_absolute() and file.is_file(), 'Captured weapon dependency file is missing.')
        file = file.resolve()
        if asset.startswith('Assets/'):
            _require(file == _project_source(document, asset), 'Captured weapon dependency belongs to another Unity project.')
        meta = Path(str(file) + '.meta')
        _require(isinstance(entry['guid'], str) and re.fullmatch('[0-9a-fA-F]{32}', entry['guid'])
                 and meta.is_file() and _sha(file) == entry['fileSha256'] and _sha(meta) == entry['metaSha256'],
                 'A captured weapon source dependency or importer changed; prepare its context again.')
        guids = re.findall(r'^guid:\s*([0-9a-fA-F]{32})\s*$', meta.read_text(encoding='utf-8-sig'), re.MULTILINE)
        _require(guids == [entry['guid']], 'Captured weapon dependency GUID changed.')
        _require(asset not in records, 'Captured weapon source dependency is duplicated.')
        records[asset] = entry
        names.append(asset)
    _require(names == sorted(names, key=lambda value: value.encode('utf-16-be')),
             'Captured weapon source dependency order differs from the Unity ordinal inventory.')
    ignored = []
    for entry in excluded:
        _require(isinstance(entry, dict) and set(entry) == {'assetPath', 'guid', 'reason'},
                 'Invalid declared Unity dependency exclusion.')
        asset, reason = entry['assetPath'], entry['reason']
        _require(isinstance(asset, str) and asset not in records and asset not in ignored
                 and ((reason == 'package-no-physical-file' and asset.startswith('Packages/'))
                      or (reason == 'unity-builtin-no-physical-file' and asset in {
                          'Resources/unity_builtin_extra', 'Library/unity default resources',
                          'Resources/unity default resources'})), 'An actual source file cannot be silently excluded.')
        ignored.append(asset)
    _require(ignored == sorted(ignored, key=lambda value: value.encode('utf-16-be')),
             'Declared Unity dependency exclusions changed order.')
    for prefix in ('model', 'prefab'):
        entry = records.get(document[prefix + 'AssetPath'])
        file_hash = document['modelSha256'] if prefix == 'model' else document['prefabFileSha256']
        _require(entry is not None and entry['guid'] == document[prefix + 'Guid']
                 and entry['fileSha256'] == file_hash and entry['metaSha256'] == document[prefix + 'MetaSha256'],
                 'Both actual weapon source roots must belong to the captured dependency inventory.')
    return len(sources), len(excluded)


def validate_context(spec):
    spec = contract.validate(spec)
    return _validate_context_identity(spec)


def _validate_context_identity(spec):
    path = Path(spec['weaponContextFile']).resolve()
    _require(path.is_file() and path.stat().st_size <= 128 * 1024 * 1024
             and _sha(path) == spec['weaponContextSha256'], 'Capture the current actual Unity weapon context first.')
    document = json.loads(path.read_text(encoding='utf-8-sig'))
    _require(isinstance(document, dict) and document.get('schema') == SCHEMA
             and document.get('units') == 'metres' and document.get('coordinate') == 'unity-lh-y-up'
             and document.get('matrixLayout') == 'row-major'
             and document.get('geometrySpace') == 'prefab-local; prefab root TRS excluded',
             'Unsupported captured weapon coordinate space.')
    _require(all(isinstance(document.get(key), str) and document[key] for key in (
        'modelFile', 'modelAssetPath', 'modelGuid', 'modelSha256', 'modelMetaSha256',
        'prefabAssetPath', 'prefabGuid', 'prefabFileSha256', 'prefabMetaSha256')),
        'Captured weapon source identity is incomplete.')
    _require(document.get('prefabGuid') == spec['weaponPrefabGuid']
             and document.get('modelGuid') == spec['weaponGuid']
             and document.get('modelSha256') == spec['weaponModelSha256']
             and Path(document['modelFile']).resolve() == Path(spec['weaponModelFile']).resolve(),
             'Weapon context belongs to another actual prefab/model revision.')
    model = Path(document['modelFile']).resolve()
    prefab = _project_source(document, document['prefabAssetPath'])
    _require(model.suffix.lower() == '.fbx' and model.is_file() and prefab.is_file()
             and _sha(model) == document['modelSha256'] and _sha(prefab) == document['prefabFileSha256'],
             'Captured weapon source changed; prepare its context again.')
    for source, field in ((model, 'modelMetaSha256'), (prefab, 'prefabMetaSha256')):
        meta = Path(str(source) + '.meta')
        _require(meta.is_file() and isinstance(document.get(field), str)
                 and _sha(meta) == document[field], 'Captured weapon importer/GUID metadata changed or lacks a proof.')
    _source_dependencies(document)
    prefab_matrix = _matrix(document.get('spawnedPrefabRootMatrix'))
    expected = _matrix(spec['weaponPrefabLocalMatrix'])
    _require(max(abs(prefab_matrix[r][c] - expected[r][c]) for r in range(4) for c in range(4)) <= 2e-5,
             'Captured weapon prefab scale differs from the composition.')
    for key in ('modelRoot', 'prefabRoot'):
        _require(isinstance(document.get(key), dict), 'Captured weapon root transform is missing.')
        _matrix(document[key].get('matrix'))
    importer = document.get('importer')
    _require(isinstance(importer, dict) and all(type(importer.get(key)) in (int, float)
        and math.isfinite(importer[key]) and importer[key] > 0 for key in ('globalScale', 'fileScale'))
        and all(type(importer.get(key)) is bool for key in ('useFileScale', 'bakeAxisConversion')),
        'Actual captured ModelImporter units/axis settings are incomplete.')
    meshes = document.get('meshes')
    _require(isinstance(meshes, list) and meshes, 'Captured weapon has no actual mesh geometry.')
    points, identities = [], set()
    from mathutils import Vector
    for mesh in meshes:
        _require(isinstance(mesh, dict) and type(mesh.get('enabled')) is bool
                 and type(mesh.get('activeInPrefab')) is bool, 'Invalid captured weapon renderer.')
        _require(isinstance(mesh.get('rendererPath'), str) and isinstance(mesh.get('rendererType'), str),
                 'Captured weapon renderer identity is incomplete.')
        identity = (mesh['rendererPath'], mesh['rendererType'])
        _require(identity not in identities, 'Captured weapon renderer identity is ambiguous.')
        identities.add(identity)
        _require(mesh.get('meshGuid') == document['modelGuid']
                 and mesh.get('meshAssetPath') == document['modelAssetPath']
                 and isinstance(mesh.get('modelRendererPaths'), list) and mesh['modelRendererPaths'],
                 'An external weapon mesh has no captured source proof; prepare an FBX-backed display context.')
        count = mesh.get('vertexCount')
        vertices, triangles = mesh.get('renderedVertices'), mesh.get('triangles')
        _require(type(count) is int and 0 < count <= 250000 and isinstance(vertices, list)
                 and len(vertices) == count and isinstance(triangles, list) and triangles
                 and len(triangles) % 3 == 0 and all(type(i) is int and 0 <= i < count for i in triangles),
                 'Captured weapon renderer has incomplete actual geometry.')
        converted = [_vector(v) for v in vertices]
        matrix = _matrix(mesh.get('rendererLocalToPrefab'))
        if mesh['enabled'] and mesh['activeInPrefab']:
            points.extend(tuple(matrix @ Vector(converted[index])) for index in sorted(set(triangles)))
    _require(4 <= len(points) <= 250000, 'Captured weapon surface exceeds the supported geometry proof.')
    _require(_sha(path) == spec['weaponContextSha256'], 'Weapon context changed during validation.')
    return document, points


def _context_capture(context):
    scene = context.scene
    return {'scene': scene, 'view_layer': context.view_layer, 'active': context.view_layer.objects.active,
        'selected': tuple(context.selected_objects), 'mode': context.object.mode if context.object else 'OBJECT',
        'frame': scene.frame_current, 'subframe': scene.frame_subframe,
        'start': scene.frame_start, 'end': scene.frame_end,
        'fps': scene.render.fps, 'fps_base': scene.render.fps_base,
        'auto_key': scene.tool_settings.use_keyframe_insert_auto,
        'unit_system': scene.unit_settings.system,
        'unit_scale': float(scene.unit_settings.scale_length)}


def _restore_context(context, saved):
    import bpy
    if context.object and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    context.window.scene = saved['scene']
    context.window.view_layer = saved['view_layer']
    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in saved['selected']:
        if obj.name in context.scene.objects:
            obj.select_set(True)
    context.view_layer.objects.active = saved['active']
    if saved['active'] and saved['mode'] != 'OBJECT':
        bpy.ops.object.mode_set(mode=saved['mode'])
    scene = saved['scene']
    scene.frame_start, scene.frame_end = saved['start'], saved['end']
    scene.render.fps, scene.render.fps_base = saved['fps'], saved['fps_base']
    scene.tool_settings.use_keyframe_insert_auto = saved['auto_key']
    scene.unit_settings.system = saved['unit_system']
    scene.unit_settings.scale_length = saved['unit_scale']
    scene.frame_set(saved['frame'], subframe=saved['subframe'])
    context.view_layer.update()


def _raw_surface(context, objects):
    points = []
    from mathutils import Vector
    depsgraph = context.evaluated_depsgraph_get()
    for obj in objects:
        if obj.type != 'MESH':
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            indices = sorted({index for triangle in mesh.loop_triangles for index in triangle.vertices})
            points.extend(tuple(evaluated.matrix_world @ Vector(mesh.vertices[index].co)) for index in indices)
        finally:
            evaluated.to_mesh_clear()
    _require(4 <= len(points) <= 250000, 'Weapon display has no supported actual surface geometry.')
    return points


def _weapon_identity(spec):
    return contract.digest({key: spec[key] for key in ('weaponContextSha256', 'weaponModelSha256',
        'weaponPrefabGuid', 'weaponRigBonePath', 'weaponLocalMatrix', 'weaponPrefabLocalMatrix',
        'sourceRestSha256', 'gripTargets')})


def _rig_actions(rig):
    from .animation_composition_export import _action_hash
    ad = rig.animation_data
    pairs = [(ad.action, ad.action_slot)] if ad.action else []
    for track in ad.nla_tracks:
        for strip in track.strips:
            if strip.type == 'CLIP' and strip.action:
                pairs.append((strip.action, strip.action_slot))
    return [(action, slot.handle if slot is not None else 0,
             _action_hash(action, slot.handle if slot is not None else 0)) for action, slot in pairs]


def _artist_fields(rig, scene):
    """Exact values behind the existing artist digest, for bounded failure diagnostics."""
    ua, ad = _host('unity_animation'), rig.animation_data
    animation = None if ad is None else {
        'action': ad.action.as_pointer() if ad.action is not None else None,
        'slot': ad.action_slot.handle if ad.action_slot is not None else 0,
        'use_nla': ad.use_nla, 'use_tweak_mode': ad.use_tweak_mode,
        'blend': ad.action_blend_type, 'influence': ad.action_influence,
        'extrapolation': ad.action_extrapolation}
    return {'rest': ua._rest_state(rig.data), 'data': rig.data.as_pointer(),
        'object': {field: list(getattr(rig, field)) for field in
            ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale',
             'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')},
        'world': [float(value) for row in rig.matrix_world for value in row],
        'animation': animation,
        'pose': {pb.name: {field: list(getattr(pb, field)) for field in
            ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')}
            for pb in rig.pose.bones},
        'frame': scene.frame_current, 'subframe': scene.frame_subframe,
        'fps': scene.render.fps, 'fps_base': scene.render.fps_base,
        'units': {'system': scene.unit_settings.system,
                  'scale_length': scene.unit_settings.scale_length}}


def _first_artist_changes(before, after):
    """Report at most eight changed scalar values; never dump all poses or Actions."""
    pending, changes = [('artist', before, after)], []
    while pending and len(changes) < 8:
        path, left, right = pending.pop()
        if contract.canonical_bytes(left) == contract.canonical_bytes(right):
            continue
        if isinstance(left, dict) and isinstance(right, dict):
            pending.extend((path + '.' + key, left.get(key, '<missing>'), right.get(key, '<missing>'))
                           for key in reversed(sorted(set(left) | set(right))))
        elif isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
            pending.extend((path + '[%d]' % index, left[index], right[index])
                           for index in reversed(range(len(left))))
        else:
            changes.append({'field': path, 'before': left, 'after': right})
    return changes


_ASSET_TABLES = ('meshes', 'armatures', 'curves', 'cameras', 'lights', 'materials',
                 'node_groups', 'textures', 'images', 'actions')


def _asset_snapshot():
    import bpy
    return {key: set(getattr(bpy.data, key)) for key in _ASSET_TABLES}


def _new_assets(previous):
    import bpy
    return {key: tuple(block for block in getattr(bpy.data, key) if block not in previous[key])
            for key in _ASSET_TABLES}


def _remove_unused_assets(assets):
    import bpy
    # Object-owned geometry releases materials, which in turn release images.
    # Repeated passes also handle a newly imported node-group dependency chain.
    remaining = [(key, block) for key in _ASSET_TABLES for block in assets.get(key, ())]
    while remaining:
        retained, removed = [], False
        for key, block in remaining:
            if block.users == 0:
                getattr(bpy.data, key).remove(block)
                removed = True
            else:
                retained.append((key, block))
        if not removed:
            return
        remaining = retained


def _attachment_basis(scope, packet, native, index):
    unity_rest = _host('unity_animation')._matrix(packet['bones'][index]['rest'], native)
    return scope.mapping.rest_world[native].inverted() @ scope.mapping.conversion @ unity_rest


def _bone_anchor(collection, created, owner, rig, native, label):
    import bpy
    anchor = bpy.data.objects.new(label, None)
    created.append(anchor)
    anchor[OWNER] = owner
    collection.objects.link(anchor)
    constraint = anchor.constraints.new('COPY_TRANSFORMS')
    constraint.name = 'Actual Authored Bone Mount'
    constraint.target, constraint.subtarget = rig, native
    constraint.target_space, constraint.owner_space = 'WORLD', 'WORLD'
    return anchor


def validate_owned(rig):
    import bpy
    record = rig.get(RECORD)
    _require(record is not None and record.get('schema') == SCHEMA, 'No owned actual weapon context is attached.')
    owner, collection = record.get('owner'), record.get('collection')
    _require(isinstance(owner, str) and re.fullmatch(
        r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', owner),
        'Weapon context has no valid generated ownership identity.')
    _require(isinstance(collection, bpy.types.Collection) and OWNER in collection
             and collection.get(OWNER) == owner,
             'Weapon display collection ownership changed.')
    scene = record.get('scene')
    _require(isinstance(scene, bpy.types.Scene)
             and ('scene_root' not in record or record.get('scene_root') is scene.collection),
             'Weapon display Scene ownership changed.')
    root = scene.collection
    parents = {parent for parent in (*bpy.data.collections, *(s.collection for s in bpy.data.scenes))
               if collection in tuple(parent.children)}
    _require(parents == {root}, 'Weapon display collection moved or gained an external Scene link.')
    objects = tuple(record['objects'].values())
    _require(objects and set(collection.objects) == set(objects) and all(isinstance(obj, bpy.types.Object)
        and OWNER in obj and obj.get(OWNER) == owner and set(obj.users_collection) == {collection} for obj in objects),
        'Weapon display objects gained an unowned link.')
    for key in _ASSET_TABLES:
        table = getattr(bpy.data, key)
        for block in record.get('assets', {}).get(key, {}).values():
            _require(isinstance(block, bpy.types.ID) and table.get(block.name) is block
                     and OWNER in block and block.get(OWNER) == owner, 'Weapon display asset ownership changed.')
    return record, objects


def remove_context(context, rig):
    import bpy
    record, objects = validate_owned(rig)
    collection = record['collection']
    scene, root = record['scene'], record['scene'].collection
    assets = {key: tuple(record.get('assets', {}).get(key, {}).values()) for key in _ASSET_TABLES}
    allowed = {rig, collection, *objects}
    users = bpy.data.user_map()
    for obj in objects:
        _require(all(owner in allowed for owner in users.get(obj, set())),
                 'An external object uses this weapon context; preserve it for inspection.')
    _require(collection.users == 2 and all(owner in {rig, scene, root}
        for owner in users.get(collection, set())), 'Weapon context collection gained an external user.')
    del rig[RECORD]
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(collection)
    _remove_unused_assets(assets)


def add_context(context, rig, spec, packet, scope):
    """Display captured Unity surfaces; do not automatically bind hand IK to them."""
    from .animation_composition_controls import validate_scope
    spec = contract.validate(spec)
    validate_scope(context, rig, spec, packet, scope)
    document, expected = validate_context(spec)
    return _add_display(context, rig, spec, packet, scope.mapping,
        document=document, expected=expected, identity=_weapon_identity(spec),
        attachment_basis=lambda native, index: _attachment_basis(scope, packet, native, index),
        revalidate=lambda: validate_context(spec), grips=spec['gripTargets'])


def _add_display(context, rig, spec, packet, mapping, *, document, expected,
                 identity, attachment_basis, revalidate, grips, rigging_owner=None):
    """Display captured Unity surfaces; do not automatically bind hand IK to them."""
    import bpy
    from mathutils import Matrix, Vector
    from .animation_composition_export import _artist_state, _action_hash
    if rig.get(RECORD):
        record, _ = validate_owned(rig)
        _require(record.get('identity') == identity, 'Remove the previous owned weapon context before changing its source or mount.')
        _require(record.get('rigging_session_owner') == rigging_owner,
                 'The owned weapon display belongs to another explicit preview session.')
        return json.loads(record['proof_json'])
    indices = {bone['path']: index for index, bone in enumerate(packet['bones'])}
    bone_index = indices.get(spec['weaponRigBonePath'])
    _require(bone_index is not None, 'Actual weapon mount has no exact packet bone path.')
    native = next((name for name, index in mapping.indices.items() if index == bone_index), None)
    _require(native is not None and native in rig.pose.bones, 'Actual weapon mount is unmapped on the original Rig.')
    saved = _context_capture(context)
    _require(saved['mode'] != 'EDIT', 'Leave Edit Mode before loading the actual weapon context.')
    _require(math.isfinite(saved['unit_scale']) and saved['unit_scale'] > 0, 'Invalid artist Scene unit scale.')
    rest_before = contract.digest(_host('unity_animation')._rest_state(rig.data))
    artist_before, actions_before = _artist_state(rig, context.scene), _rig_actions(rig)
    artist_fields_before = _artist_fields(rig, context.scene)
    _require(contract.digest(artist_fields_before) == artist_before,
             'Weapon state diagnostics differ from the original artist-state contract.')
    action, slot = rig.animation_data.action, rig.animation_data.action_slot
    slot_pointer = slot.as_pointer() if slot is not None else None
    slot_handle = rig.animation_data.action_slot_handle if slot is not None else None
    old_objects = set(bpy.data.objects)
    old_assets = _asset_snapshot()
    collection, created, meshes, displayed = None, [], [], []
    try:
        _require(context.scene.unit_settings.system == saved['unit_system']
                 and float(context.scene.unit_settings.scale_length) == saved['unit_scale'],
                 'Loading the weapon changed the artist Scene units; restore them before retrying.')
        owner = str(uuid.uuid4())
        collection = bpy.data.collections.new('Weapon Context')
        collection[OWNER] = owner
        context.scene.collection.children.link(collection)
        # The packet bone's authored basis can differ from the artist bone axis.
        # Solve the local attachment once from their authenticated Rest frames.
        ua = _host('unity_animation')
        basis = attachment_basis(native, bone_index)
        authored_local = basis @ _matrix(spec['weaponLocalMatrix']) @ _matrix(spec['weaponPrefabLocalMatrix'])
        anchor = _bone_anchor(collection, created, owner, rig, native, 'Weapon Mount')
        geometry = bpy.data.objects.new('Weapon Geometry', None)
        created.append(geometry)
        geometry[OWNER] = owner
        collection.objects.link(geometry)
        geometry.parent = anchor
        geometry.matrix_parent_inverse = Matrix.Identity(4)
        geometry.matrix_basis = authored_local
        vertex_count = triangle_count = 0
        for captured in document['meshes']:
            if not captured['enabled'] or not captured['activeInPrefab']:
                continue
            label = 'Weapon Surface %02d · %s' % (
                len(displayed) + 1, captured['rendererPath'].rsplit('/', 1)[-1] or 'Mesh')
            mesh = bpy.data.meshes.new(label)
            meshes.append(mesh)
            mesh[OWNER] = owner
            vertices = [_vector(value) for value in captured['renderedVertices']]
            triangles = captured['triangles']
            mesh.from_pydata(vertices, [], [tuple(triangles[index:index + 3])
                                           for index in range(0, len(triangles), 3)])
            mesh.update()
            obj = bpy.data.objects.new(label, mesh)
            created.append(obj)
            obj[OWNER] = owner
            obj['captured_renderer_path'] = captured['rendererPath']
            obj.hide_select = True
            collection.objects.link(obj)
            obj.parent = geometry
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = _matrix(captured['rendererLocalToPrefab'])
            displayed.append(obj)
            vertex_count += len(vertices)
            triangle_count += len(triangles) // 3
        proof = {'captured_unity_geometry': True, 'captured_renderer_count': len(displayed),
                 'captured_vertex_count': vertex_count, 'captured_triangle_count': triangle_count}
        # These are actual captured targets relative to their declared parent
        # bone. Displaying them does not install an IK or hand correction.
        by_index = {index: name for name, index in mapping.indices.items()}
        for grip in grips:
            index = indices[grip['rigBonePath']]
            parent_native = by_index[index]
            grip_anchor = _bone_anchor(collection, created, owner, rig, parent_native,
                                       'Grip Parent · ' + grip['id'])
            target = bpy.data.objects.new('Grip · ' + grip['id'], None)
            created.append(target)
            target[OWNER], target['authored_grip_id'] = owner, grip['id']
            target['authored_control_id'] = grip['controlId']
            target.empty_display_type, target.empty_display_size = 'ARROWS', .03
            collection.objects.link(target)
            target.parent = grip_anchor
            target.matrix_parent_inverse = Matrix.Identity(4)
            target.matrix_basis = attachment_basis(parent_native, index) @ _matrix(grip['localMatrix'])
        context.view_layer.update()
        evaluated = rig.evaluated_get(context.evaluated_depsgraph_get())
        expected_world = evaluated.matrix_world @ evaluated.pose.bones[native].matrix @ authored_local
        unit_scale = saved['unit_scale']
        placed_expected = [tuple((expected_world @ Vector(point)) * unit_scale) for point in expected]
        placed_actual = [tuple(Vector(point) * unit_scale) for point in _raw_surface(context, displayed)]
        proof.update(compare_surface_points(placed_actual, placed_expected))
        revalidate()
        rest_after = contract.digest(ua._rest_state(rig.data))
        artist_after = _artist_state(rig, context.scene)
        failures = {}
        if rest_after != rest_before:
            failures['rest'] = {'before': rest_before, 'after': rest_after}
        current_action = rig.animation_data.action
        if current_action is not action:
            failures['action'] = {'python_identity_preserved': False,
                'before_pointer': action.as_pointer() if action is not None else None,
                'after_pointer': current_action.as_pointer() if current_action is not None else None}
        current_slot = rig.animation_data.action_slot
        current_slot_pointer = current_slot.as_pointer() if current_slot is not None else None
        current_slot_handle = rig.animation_data.action_slot_handle if current_slot is not None else None
        if (current_slot_pointer, current_slot_handle) != (slot_pointer, slot_handle):
            failures['slot'] = {'python_identity_preserved': current_slot is slot,
                'before_pointer': slot_pointer, 'after_pointer': current_slot_pointer,
                'before_handle': slot_handle, 'after_handle': current_slot_handle}
        if artist_after != artist_before:
            fields_after = _artist_fields(rig, context.scene)
            _require(contract.digest(fields_after) == artist_after,
                     'Weapon state diagnostics differ from the original artist-state contract.')
            failures['artist'] = {'before_sha256': artist_before, 'after_sha256': artist_after,
                                 'first_changes': _first_artist_changes(artist_fields_before, fields_after)}
        changed_actions = []
        for referenced, handle, fingerprint in actions_before:
            current_hash = _action_hash(referenced, handle)
            if current_hash != fingerprint and len(changed_actions) < 8:
                changed_actions.append({'action': referenced.name, 'slot': handle,
                                        'before': fingerprint, 'after': current_hash})
        if changed_actions:
            failures['action_content'] = changed_actions
        _require(not failures,
                 'Loading weapon context changed the original Rest, pose, time or Action content/slot: ' +
                 json.dumps(failures, allow_nan=False, separators=(',', ':')))
        proof.update({'schema': SCHEMA, 'context_sha256': spec['weaponContextSha256'],
            'source_sha256': spec['weaponModelSha256'], 'native_mount': native,
            'prefab_scale_applied_once': True, 'actual_fbx_import': False,
            'artist_unit_system': saved['unit_system'], 'artist_unit_scale': unit_scale,
            'authored_grip_count': len(grips), 'automatic_hand_ik': False,
            'grip_limitation': ('Authored grip targets are displayed; supported FK controls remain the edit inputs.'
                if grips else 'No authored named grip targets; edit supported FK controls against the actual weapon.')})
        imported_assets = _new_assets(old_assets)
        _require(set(bpy.data.objects) - old_objects == set(created)
                 and all(set(blocks) == (set(meshes) if key == 'meshes' else set())
                         for key, blocks in imported_assets.items()),
                 'Loading the weapon introduced an unowned native object or asset.')
        rig[RECORD] = {'schema': SCHEMA, 'owner': owner, 'identity': identity, 'collection': collection,
            'scene': saved['scene'],
            'assets': {key: {str(i): block for i, block in enumerate(blocks)}
                       for key, blocks in imported_assets.items()},
            'anchor': anchor, 'geometry_root': geometry, 'objects': {str(i): obj for i, obj in enumerate(created)},
            'proof_json': json.dumps(proof, allow_nan=False, separators=(',', ':'))}
        if rigging_owner is not None:
            proof.update({'display_owner': owner, 'identity': identity,
                          'rigging_session_owner': rigging_owner, 'record_key': RECORD,
                          'grip_limitation': 'Display follows the captured Right-hand Socket; Target/Hint inputs remain independent.'})
            rig[RECORD]['rigging_session_owner'] = rigging_owner
            rig[RECORD]['proof_json'] = json.dumps(proof, allow_nan=False, separators=(',', ':'))
        validate_owned(rig)
        return proof
    except Exception:
        if RECORD in rig:
            del rig[RECORD]
        for obj in reversed(created):
            bpy.data.objects.remove(obj, do_unlink=True)
        if collection:
            bpy.data.collections.remove(collection)
        _remove_unused_assets({'meshes': tuple(meshes)})
        raise


def _rigging_context_admission(context, rig, preview, packet, mapping, session):
    from . import animation_rigging_contract as rigging_contract
    native = _host('unity_animation_rigging')
    current = native.current(rig)
    _require(current is not None and current['rig'] is rig
             and current['scene'] is context.scene
             and session['rig'] is rig and session['scene'] is context.scene,
             'Weapon preview needs the actual owning Rigging session and Scene.')
    for key in ('owner', 'base_slot', 'input_slot', 'source_packet_sha256',
                'work_item_id', 'new_action_id', 'source_read_only', 'data_name', 'rest_sha256'):
        _require(current[key] == session[key], 'Weapon preview session identity changed: ' + key)
    for key in ('base_action', 'input_action'):
        _require(current[key].as_pointer() == session[key].as_pointer(),
                 'Weapon preview cannot replace its native base or input Action.')
    capture = rigging_contract.validate_capture(session['capture'], packet=packet)
    _require(contract.canonical_bytes(capture) == contract.canonical_bytes(current['capture'])
             and contract.canonical_bytes(capture.get('weaponPreview')) == contract.canonical_bytes(preview)
             and packet.get('_sha256') == current['source_packet_sha256'],
             'Weapon display does not belong to this authenticated source packet and capture.')
    _require(preview is not None, 'This Rigging capture has no explicit weapon preview.')
    mount, socket = preview['weaponRigBonePath'], preview['weaponSocketPath']
    _require(mount == capture['bindings'][0]['tipPath']
             and socket.rsplit('/', 1)[0] == mount and socket != mount,
             'The actual weapon Socket must be a direct child of the declared Right hand.')
    _require(rig.data.name == current['data_name']
             and contract.digest(_host('unity_animation')._rest_state(rig.data)) == current['rest_sha256'],
             'Weapon preview cannot replace the original native Rest skeleton.')
    indices = {bone['name']: index for index, bone in enumerate(packet['bones'])
               if bone['name'] in rig.data.bones}
    _require(len(indices) == sum(bone['name'] in rig.data.bones for bone in packet['bones'])
             and mapping.indices == indices
             and [float(value) for row in mapping.conversion for value in row] == current['conversion']
             and set(mapping.rest_world) == set(indices),
             'Weapon preview needs the exact original packet-to-Rig mapping.')
    for name in indices:
        rest = rig.matrix_world @ rig.data.bones[name].matrix_local
        _require([float(value) for row in mapping.rest_world[name] for value in row]
                 == [float(value) for row in rest for value in row],
                 'The weapon preview mapping contains a foreign native Rest frame.')
    bone_indices = [index for index, bone in enumerate(packet['bones']) if bone['path'] == mount]
    _require(len(bone_indices) == 1
             and sum(index == bone_indices[0] for index in mapping.indices.values()) == 1,
             'Weapon mount has no unique exact native packet bone.')
    identity = contract.digest({'schema': 'cdesigner.rigging-weapon-preview/1',
        'session_owner': current['owner'], 'source_packet_sha256': current['source_packet_sha256'],
        'source_rest_sha256': current['rest_sha256'], 'weapon_preview': preview})
    return identity


def add_rigging_context(context, rig, preview, packet, mapping, *, session):
    """Display one authenticated Socket mount; never install or parent hand inputs."""
    if preview is None:
        _require(session['capture'].get('weaponPreview') is None,
                 'An explicit weapon preview cannot be silently omitted.')
        return None
    identity = _rigging_context_admission(context, rig, preview, packet, mapping, session)
    document, expected = _validate_context_identity(preview)
    def basis(name, index):
        unity_rest = _host('unity_animation')._matrix(packet['bones'][index]['rest'], name)
        return mapping.rest_world[name].inverted() @ mapping.conversion @ unity_rest
    def revalidate():
        _require(_rigging_context_admission(context, rig, preview, packet, mapping, session) == identity,
                 'Weapon preview identity changed while displaying its actual geometry.')
        _validate_context_identity(preview)
    return _add_display(context, rig, preview, packet, mapping,
        document=document, expected=expected, identity=identity, attachment_basis=basis,
        revalidate=revalidate, grips=(), rigging_owner=session['owner'])


def remove_rigging_context(context, rig, *, session):
    """Close only this session's display through the unchanged native ownership guards."""
    proof = session.get('weapon_preview')
    if proof is None:
        return
    current = _host('unity_animation_rigging').current(rig)
    _require(current is not None and current['owner'] == session['owner']
             and current['rig'] is rig and current['scene'] is context.scene,
             'Close the weapon only through its actual owning Rigging session.')
    _require(isinstance(proof, dict) and proof.get('record_key') == RECORD
             and proof.get('rigging_session_owner') == session['owner'],
             'The Rigging weapon display has no exact session ownership proof.')
    record, _ = validate_owned(rig)
    _require(record.get('rigging_session_owner') == session['owner']
             and record['owner'] == proof['display_owner'] and record['identity'] == proof['identity'],
             'Cannot remove another session or legacy weapon display.')
    remove_context(context, rig)
