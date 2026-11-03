"""Tiny private native save/move/reopen witness for dependency relocation.

No artist file, Unity, worker, preferences save or library reload is used.
Run through run_trusted_blender_validation.py with a fresh CONFIG.
"""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_cd_dependency_native'
module = ModuleType(PACKAGE)
module.__path__ = [str(ROOT / 'addons' / 'character_designer')]
sys.modules[PACKAGE] = module
from _cd_dependency_native import animation_worklist as worklist
from _cd_dependency_native import animation_worklist_dependencies as dependencies


class DependencyFixtureRow(bpy.types.PropertyGroup):
    item_id: StringProperty()
    work_item_id: StringProperty()
    clip_key: StringProperty()
    source_file: StringProperty()
    source_hash: StringProperty()
    source_slot: IntProperty()
    new_animation: BoolProperty()
    current_rig_revision: StringProperty()
    rig: PointerProperty(type=bpy.types.Object)
    source_action: PointerProperty(type=bpy.types.Action)
    custom_action: PointerProperty(type=bpy.types.Action)


class DependencyFixtureState(bpy.types.PropertyGroup):
    rig: PointerProperty(type=bpy.types.Object)
    workspace_identity: StringProperty()
    items: CollectionProperty(type=DependencyFixtureRow)
    retired_items: CollectionProperty(type=DependencyFixtureRow)


def content(action):
    return sorted((curve.data_path, curve.array_index,
        [(tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
          point.interpolation) for point in curve.keyframe_points])
        for layer in action.layers for strip in layer.strips
        for bag in strip.channelbags for curve in bag.fcurves)


def main():
    bpy.utils.register_class(DependencyFixtureRow)
    bpy.utils.register_class(DependencyFixtureState)
    bpy.types.Scene.character_designer_animation_worklist = PointerProperty(type=DependencyFixtureState)
    # All files are unique and private; keep the directory as native evidence.
    evidence_parent = Path(r'D:\Blender\Projects\Character\X\validation\animation_dependencies_20261102')
    evidence_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='portable-', dir=evidence_parent)).resolve()
    original = root / 'original'
    original.mkdir()
    sources = root / 'source-cache'
    sources.mkdir()
    scene = bpy.context.scene
    data = bpy.data.armatures.new('Dependency Native Skeleton')
    rig = bpy.data.objects.new('Dependency Native Rig', data)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = data.edit_bones.new('root')
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.pose.bones['root'].location = (0.1, 0.2, 0.3)
    rig.pose.bones['root'].keyframe_insert('location', frame=1)
    rig.pose.bones['root'].location = (0.4, 0.5, 0.6)
    rig.pose.bones['root'].keyframe_insert('location', frame=9)
    custom = rig.animation_data.action
    custom.name = 'Native Custom'
    source = custom.copy()
    source.name = 'Native Source'
    library_file = sources / 'source.blend'
    bpy.data.libraries.write(str(library_file), {source}, fake_user=True, compress=True)
    source.use_fake_user = False
    bpy.data.actions.remove(source)
    with bpy.data.libraries.load(str(library_file), link=True) as (available, requested):
        requested.actions = [name for name in available.actions if name == 'Native Source']
    source = requested.actions[0]
    assert source is not None and source.library is not None
    saved = scene.character_designer_animation_worklist
    saved.rig, saved.workspace_identity = rig, 'native exact dependency workspace'
    row = saved.items.add()
    row.item_id, row.work_item_id, row.clip_key = 'native-row', 'native-work', 'native-clip'
    row.rig, row.source_action, row.custom_action = rig, source, custom
    row.source_file, row.source_hash = str(library_file), hashlib.sha256(library_file.read_bytes()).hexdigest()
    row.source_slot = source.slots[0].handle
    logical = row.source_file
    before_source, before_custom = content(source), content(custom)
    scene_file = original / 'editing.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(scene_file), check_existing=False) == {'FINISHED'}
    package = worklist.package_dependencies(bpy.context, original / 'dependencies')
    rebound = worklist.rebind_dependencies(bpy.context, package['manifest_path'])
    assert rebound['portable_paths'] is True
    assert row.source_file == logical
    assert source.library.filepath.startswith('//')
    assert bpy.ops.wm.save_as_mainfile(filepath=str(scene_file), check_existing=False) == {'FINISHED'}
    saveas = root / 'saveas'
    saveas.mkdir()
    saveas_file = saveas / 'editing.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(saveas_file), check_existing=False,
                                     relative_remap=True) == {'FINISHED'}
    worklist._check_baseline(row)
    assert content(row.custom_action) == before_custom
    # The new save location can explicitly acquire its own portable package.
    package = worklist.package_dependencies(bpy.context, saveas / 'dependencies')
    assert worklist.rebind_dependencies(bpy.context, package['manifest_path'])['portable_paths'] is True
    assert bpy.ops.wm.save_as_mainfile(filepath=str(saveas_file), check_existing=False) == {'FINISHED'}
    moved = root / 'moved'
    shutil.copytree(saveas, moved)
    # Renames make the old paths unavailable, without deleting any evidence.
    original.rename(root / 'original-retained')
    saveas.rename(root / 'saveas-retained')
    sources.rename(root / 'source-cache-retained')
    assert not library_file.exists() and not scene_file.exists()
    assert bpy.ops.wm.open_mainfile(filepath=str(moved / 'editing.blend'), use_scripts=True) == {'FINISHED'}
    saved = bpy.context.scene.character_designer_animation_worklist
    row = saved.items[0]
    assert row.source_file == logical
    assert row.source_action.library is not None
    assert not row.source_action.is_missing
    assert content(row.source_action) == before_source
    assert content(row.custom_action) == before_custom
    assert Path(dependencies.source_path(row)).is_relative_to(moved)
    worklist._check_baseline(row)
    # A second explicit locate uses the moved manifest without changing keys.
    moved_result = worklist.rebind_dependencies(bpy.context, moved / 'dependencies' / dependencies.MANIFEST)
    assert moved_result['portable_paths'] is True
    assert content(row.custom_action) == before_custom
    result = dict(passed=True, sources=package['sources'], checks=[
        'native linked Source retained', 'logical row path exact', 'relative paths persisted',
        'private save success', 'native Save As to another directory',
        'exact verified absolute fallback after native relative remap',
        'new Save As location repackaged', 'old source and saved-file paths unavailable',
        'moved pair reopened', 'Source content exact', 'Custom content exact',
        'full Source hash check', 'second explicit locate accepted'], evidence=str(root))
    (root / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('DEPENDENCY_NATIVE_RESULT=' + json.dumps(result))
    del bpy.types.Scene.character_designer_animation_worklist
    bpy.utils.unregister_class(DependencyFixtureState)
    bpy.utils.unregister_class(DependencyFixtureRow)


if __name__ == '__main__':
    main()
