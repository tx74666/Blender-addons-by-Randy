"""Project-local omissions from the Unity motion catalog. Native Actions stay owned by their worklist."""

import json
from pathlib import Path
import re

import bpy


def project_rules():
    blend = getattr(getattr(bpy, 'data', None), 'filepath', '')
    if not blend:
        return {}
    path = Path(blend).parent / 'project_dependencies' / 'animation_catalog_exclusions.json'
    try:
        rules = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(rules, dict) or rules.get('version') != 1:
            raise ValueError('Unsupported exclusion file.')
        workspace, target, omitted = rules['workspace_id'], rules['target_guid'], rules['clip_keys']
        if not isinstance(omitted, list) or not all(isinstance(key, str) and re.fullmatch(r'work:[0-9a-f]{32}', key)
                                                    for key in omitted):
            raise ValueError('Expected work item keys.')
        return {(workspace, target): frozenset(omitted)}
    except FileNotFoundError:
        return {}
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(f'Character Designer: ignored invalid animation exclusions at {path}: {exc}')
        return {}


def keys_for(record):
    return project_rules().get((record.get('workspaceId'), (record.get('targetGuid') or '').lower()), frozenset())


def filter_saved_catalogs(_unused=None):
    rules = project_rules()
    if not rules:
        return
    for scene in bpy.data.scenes:
        state = getattr(scene, 'character_designer_animation_worklist', None)
        if state is None or scene.library or scene.override_library:
            continue
        omitted = rules.get((state.workspace_id, state.target_guid))
        if not omitted:
            continue
        rows = state.catalog
        indices = [i for i, row in enumerate(rows) if row.clip_key in omitted]
        if not indices:
            continue
        index = state.catalog_index
        for i in reversed(indices):
            rows.remove(i)
        state.catalog_index = -1 if index in indices else index - sum(i < index for i in indices)
        if state.selected_motion_clip in omitted:
            state.selected_motion_clip = ''


def register():
    handlers = bpy.app.handlers.load_post
    if filter_saved_catalogs not in handlers:
        handlers.append(bpy.app.handlers.persistent(filter_saved_catalogs))
    # Blender restricts data access while enabling an add-on. Run after registration.
    if hasattr(bpy.data, 'scenes'):
        filter_saved_catalogs()
    elif not bpy.app.timers.is_registered(filter_saved_catalogs):
        bpy.app.timers.register(filter_saved_catalogs, first_interval=0)


def unregister():
    if filter_saved_catalogs in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(filter_saved_catalogs)
    if bpy.app.timers.is_registered(filter_saved_catalogs):
        bpy.app.timers.unregister(filter_saved_catalogs)
