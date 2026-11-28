"""Import Unity's captured weapon library as reusable Blender Collection assets."""

import json
from pathlib import Path
import re

import bpy
from mathutils import Matrix, Vector

from . import animation_composition_weapon as surface
from . import animation_weapon_profiles as profiles

SCHEMA = 'cdesigner.weapon-library/1'
GUID = re.compile(r'[0-9a-f]{32}')
SHA = re.compile(r'[0-9a-f]{64}')
# Unity X right/Y up/Z forward → Blender X right/Y forward/Z up.
TO_BLENDER = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def _read(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('The Unity weapon library is larger than 1 MiB.')
    document = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(document, dict) or document.get('schema') != SCHEMA:
        raise ValueError('Select the exported Unity weapon library JSON.')
    entries = document.get('weapons')
    unavailable = document.get('unavailable', [])
    if not isinstance(entries, list) or not isinstance(unavailable, list):
        raise ValueError('The weapon library list is malformed.')
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get('name'), str)
                or not entry['name'].strip()
                or not all(isinstance(entry.get(key), str) and GUID.fullmatch(entry[key])
                           for key in ('prefabGuid', 'modelGuid'))
                or not all(isinstance(entry.get(key), str) and SHA.fullmatch(entry[key])
                           for key in ('contextSha256', 'modelSha256'))
                or not isinstance(entry.get('modelFile'), str) or not Path(entry['modelFile']).is_absolute()
                or entry['prefabGuid'] in seen):
            raise ValueError('The weapon library contains a missing or duplicate weapon identity.')
        seen.add(entry['prefabGuid'])
    return entries, unavailable


def _load_profile(folder, entry):
    checksum = entry['contextSha256']
    context_file = folder / 'weapon-contexts' / checksum / 'weapon_context.json'
    document, _ = surface._validate_context_identity({
        'weaponContextFile': str(context_file), 'weaponContextSha256': checksum,
        'weaponPrefabGuid': entry['prefabGuid'], 'weaponGuid': entry['modelGuid'],
        'weaponModelSha256': entry['modelSha256'], 'weaponModelFile': entry['modelFile'],
        'weaponPrefabLocalMatrix': entry.get('spawnedPrefabRootMatrix'),
    })
    root = TO_BLENDER @ surface._matrix(document['spawnedPrefabRootMatrix'])
    profile = profiles.new_profile(entry['name'].strip())
    objects, meshes = [], []
    try:
        for captured in document['meshes']:
            if not captured['enabled'] or not captured['activeInPrefab']:
                continue
            label = captured['meshName'] or 'Weapon Surface'
            transform = root @ surface._matrix(captured['rendererLocalToPrefab'])
            vertices = [transform @ Vector(surface._vector(value)) for value in captured['renderedVertices']]
            indices = captured['triangles']
            reverse = transform.determinant() < 0
            step = -1 if reverse else 1
            faces = [tuple(indices[index:index + 3][::step]) for index in range(0, len(indices), 3)]
            mesh = bpy.data.meshes.new(label)
            meshes.append(mesh)
            mesh.from_pydata(vertices, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(label, mesh)
            objects.append(obj)
            profile.objects.link(obj)
        if not profile.objects:
            raise ValueError('The captured weapon has no visible surfaces.')
        profile[profiles.UNITY_PREFAB] = entry['prefabGuid']
        profile[profiles.UNITY_CONTEXT] = checksum
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
            else:
                issues.append(entry['name'] + ': existing local profile retained; the Unity revision or local identity changed.')
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            issues.append(f"{entry['name']}: {error}")
    for entry in unavailable:
        if isinstance(entry, dict) and isinstance(entry.get('name'), str):
            issues.append(f"{entry['name']}: {entry.get('reason') or 'Unavailable in Unity export.'}")
    return imported, reused, issues
