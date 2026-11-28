"""Import Unity's captured weapon library as reusable Blender Collection assets."""

from array import array
import hashlib
import json
from pathlib import Path
import re

import bpy
from mathutils import Matrix, Vector

from . import animation_composition_weapon as surface
from . import animation_weapon_materials as materials
from . import animation_weapon_profiles as profiles

SCHEMAS = ('cdesigner.weapon-library/1', 'cdesigner.weapon-library/2')
SNAPSHOT_SCHEMA = 'cdesigner.weapon-library-context/1'
GUID = re.compile(r'[0-9a-f]{32}')
SHA = re.compile(r'[0-9a-f]{64}')
APPEARANCE = 'unity_weapon_preview_materials'
# Unity X right/Y up/Z forward → Blender X right/Y forward/Z up.
TO_BLENDER = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def _read(path):
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('The Unity weapon library is larger than 8 MiB.')
    document = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(document, dict) or document.get('schema') not in SCHEMAS:
        raise ValueError('Select the exported Unity weapon library JSON.')
    entries = document.get('weapons')
    unavailable = document.get('unavailable', [])
    if not isinstance(entries, list) or not isinstance(unavailable, list):
        raise ValueError('The weapon library list is malformed.')
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get('name'), str)
                or not entry['name'].strip()
                or not isinstance(entry.get('prefabGuid'), str) or not GUID.fullmatch(entry['prefabGuid'])
                or not isinstance(entry.get('contextSha256'), str) or not SHA.fullmatch(entry['contextSha256'])
                or type(entry.get('prefabSnapshot', False)) is not bool
                or entry['prefabGuid'] in seen):
            raise ValueError('The weapon library contains a missing or duplicate weapon identity.')
        if not entry.get('prefabSnapshot') and (
                not isinstance(entry.get('modelGuid'), str) or not GUID.fullmatch(entry['modelGuid'])
                or not isinstance(entry.get('modelSha256'), str) or not SHA.fullmatch(entry['modelSha256'])
                or not isinstance(entry.get('modelFile'), str) or not Path(entry['modelFile']).is_absolute()):
            raise ValueError('An FBX-backed weapon has an incomplete model identity.')
        seen.add(entry['prefabGuid'])
    return entries, unavailable


def _read_snapshot(path, entry):
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('The weapon geometry snapshot exceeds 128 MiB.')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry['contextSha256']:
        raise ValueError('The weapon geometry snapshot changed. Export the Unity library again.')
    document = json.loads(raw)
    if (not isinstance(document, dict) or document.get('schema') != SNAPSHOT_SCHEMA
            or document.get('prefabGuid') != entry['prefabGuid']
            or document.get('units') != 'metres' or document.get('coordinate') != 'unity-lh-y-up'
            or document.get('matrixLayout') != 'row-major'
            or document.get('geometrySpace') != 'prefab-local; prefab root TRS excluded'
            or document.get('spawnedPrefabRootMatrix') != entry.get('spawnedPrefabRootMatrix')):
        raise ValueError('The weapon geometry snapshot has a different identity or coordinate space.')
    meshes = document.get('meshes')
    if not isinstance(meshes, list) or not meshes:
        raise ValueError('The weapon geometry snapshot has no surfaces.')
    for mesh in meshes:
        if (not isinstance(mesh, dict) or not isinstance(mesh.get('meshName'), str)
                or mesh.get('enabled') is not True or mesh.get('activeInPrefab') is not True):
            raise ValueError('The weapon geometry snapshot has an invalid mesh.')
        vertices, triangles = mesh.get('renderedVertices'), mesh.get('triangles')
        if (not isinstance(vertices, list) or not 0 < len(vertices) <= 250000
                or not isinstance(triangles, list) or not triangles or len(triangles) % 3
                or not all(type(i) is int and 0 <= i < len(vertices) for i in triangles)):
            raise ValueError('The weapon geometry snapshot has incomplete vertices or triangles.')
    return document


def _load_geometry(folder, entry):
    checksum = entry['contextSha256']
    context_file = folder / 'weapon-contexts' / checksum / 'weapon_context.json'
    if entry.get('prefabSnapshot'):
        document = _read_snapshot(context_file, entry)
    else:
        document, _ = surface._validate_context_identity({
            'weaponContextFile': str(context_file), 'weaponContextSha256': checksum,
            'weaponPrefabGuid': entry['prefabGuid'], 'weaponGuid': entry['modelGuid'],
            'weaponModelSha256': entry['modelSha256'], 'weaponModelFile': entry['modelFile'],
            'weaponPrefabLocalMatrix': entry.get('spawnedPrefabRootMatrix'),
        })
    return (TO_BLENDER @ surface._matrix(document['spawnedPrefabRootMatrix']),
            [mesh for mesh in document['meshes'] if mesh['enabled'] and mesh['activeInPrefab']])


def _mesh_geometry(root, captured):
    transform = root @ surface._matrix(captured['rendererLocalToPrefab'])
    vertices = [transform @ Vector(surface._vector(value)) for value in captured['renderedVertices']]
    indices = captured['triangles']
    step = -1 if transform.determinant() < 0 else 1
    faces = [tuple(indices[index:index + 3][::step]) for index in range(0, len(indices), 3)]
    return vertices, faces


def _load_profile(folder, entry):
    root, records = _load_geometry(folder, entry)
    profile = profiles.new_profile(entry['name'].strip())
    objects, meshes = [], []
    try:
        for captured in records:
            label = captured['meshName'] or 'Weapon Surface'
            vertices, faces = _mesh_geometry(root, captured)
            mesh = bpy.data.meshes.new(label)
            meshes.append(mesh)
            mesh.from_pydata(vertices, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(label, mesh)
            objects.append(obj)
            profile.objects.link(obj)
        if not profile.objects:
            raise ValueError('The captured weapon has no visible surfaces.')
        if entry.get('appearance') is not None:
            materials.apply(folder, records, objects, entry['appearance'])
            profile[APPEARANCE] = True
        profile[profiles.UNITY_PREFAB] = entry['prefabGuid']
        profile[profiles.UNITY_CONTEXT] = entry['contextSha256']
        profile.asset_mark()
        profile.use_fake_user = True
        return profile
    except Exception:
        for obj in reversed(objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in reversed(meshes):
            bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(profile)
        raise


def _add_appearance_to_existing(folder, entry, profile):
    if entry.get('appearance') is None or profile.get(APPEARANCE):
        return
    root, records = _load_geometry(folder, entry)
    objects = list(profile.objects)
    if len(records) != len(objects):
        raise ValueError('Local weapon geometry was edited, so its material slots were kept.')
    for obj, captured in zip(objects, records):
        if (obj.type != 'MESH' or obj.parent is not None or not obj.matrix_basis.is_identity or
                obj.data.users != 1 or obj.data.materials or obj.data.uv_layers):
            raise ValueError('Local weapon materials, UVs or transforms were kept.')
        vertices, faces = _mesh_geometry(root, captured)
        if len(obj.data.vertices) != len(vertices) or len(obj.data.polygons) != len(faces):
            raise ValueError('Local weapon geometry was edited, so its material slots were kept.')
        coords = array('f', [0.0]) * (len(vertices) * 3)
        loops = array('i', [0]) * len(obj.data.loops)
        obj.data.vertices.foreach_get('co', coords)
        obj.data.loops.foreach_get('vertex_index', loops)
        if (coords != array('f', (axis for vertex in vertices for axis in vertex)) or
                loops != array('i', (index for face in faces for index in face))):
            raise ValueError('Local weapon geometry was edited, so its material slots were kept.')
    materials.apply(folder, records, objects, entry['appearance'])
    profile[APPEARANCE] = True


def import_library(context, filepath):
    profiles.require_idle(context)
    path = Path(bpy.path.abspath(filepath)).resolve()
    entries, unavailable = _read(path)
    existing = {}
    for collection in bpy.data.collections:
        if profiles.is_profile(collection) and collection.get(profiles.UNITY_PREFAB):
            existing.setdefault(collection[profiles.UNITY_PREFAB], []).append(collection)
    imported, reused, issues = [], [], []
    for entry in entries:
        try:
            matches = existing.get(entry['prefabGuid'], ())
            if not matches:
                imported.append(_load_profile(path.parent, entry))
            elif len(matches) == 1 and matches[0].get(profiles.UNITY_CONTEXT) == entry['contextSha256']:
                reused.append(matches[0])
                _add_appearance_to_existing(path.parent, entry, matches[0])
            else:
                issues.append(entry['name'] + ': existing local profile retained; the Unity revision or local identity changed.')
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            issues.append(f"{entry['name']}: {error}")
    for entry in unavailable:
        if isinstance(entry, dict) and isinstance(entry.get('name'), str):
            issues.append(f"{entry['name']}: {entry.get('reason') or 'Unavailable in Unity export.'}")
    return imported, reused, issues
