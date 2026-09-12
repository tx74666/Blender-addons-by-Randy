"""Widget hierarchy migration preserves module identity and removal recovery."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import widget_collections as manager, foot_controls, limb_ik
from test_limb_ik_fk_blender import build as build_leg
from test_bone_display_blender import content, sampled_poses
import test_root_control_blender as root_fixture


def fixture():
    rig = root_fixture.fixture()
    root_fixture.root.build(bpy.context, rig)
    return rig


def legacy(rig):
    """Make a valid old flat hierarchy with authoritative old record names."""
    resources = manager._resources(rig)
    for item in resources:
        leaf = item['collection']
        manager._reparent(leaf, bpy.context.scene.collection)
        leaf.name = 'Legacy ' + item['label']
        if item['key']:
            payload = json.loads(rig.data[item['key']])
            record = payload['legs'][item['side']] if item['side'] else payload
            record[item['field']] = leaf.name
            rig.data[item['key']] = json.dumps(payload)
    manager.prune_empty(bpy.context)
    return manager._resources(rig)


def layout():
    return {c.name: (tuple(p.name for p in manager._parents(c)), tuple(c.objects.keys()),
                     tuple(c.children.keys())) for c in bpy.data.collections}


def layer_node(layer, collection):
    if layer.collection == collection:
        return layer
    for child in layer.children:
        found = layer_node(child, collection)
        if found:
            return found
    return None


def records(rig, resources):
    result = {}
    for item in resources:
        if item['key']:
            result[item['key']] = json.loads(rig.data[item['key']])
    for item in resources:
        if item['key']:
            payload = result[item['key']]
            record = payload['legs'][item['side']] if item['side'] else payload
            record[item['field']] = '<collection name>'
    return result


class WidgetCollectionTests(unittest.TestCase):
    def test_per_view_layer_widget_visibility_survives_reparenting(self):
        rig, key, _ = build_leg('DIRECT_PREROLL', 'LEFT_LEG', toes=True)
        foot_controls.build(bpy.context, rig, key)
        resources = legacy(rig)
        leaf = next(item['collection'] for item in resources if item['label'] == 'Foot.L')
        view = bpy.context.view_layer
        node = layer_node(view.layer_collection, leaf)
        node.hide_viewport, node.holdout, node.indirect_only = True, True, True
        extra = bpy.context.scene.view_layers.new('Other View Layer')
        layer_node(extra.layer_collection, leaf).exclude = True
        try:
            manager.organize(bpy.context, rig)
            node = layer_node(view.layer_collection, leaf)
            self.assertEqual((node.hide_viewport, node.holdout, node.indirect_only), (True, True, True))
            self.assertTrue(layer_node(extra.layer_collection, leaf).exclude)
        finally:
            bpy.context.scene.view_layers.remove(extra)

    def test_legacy_migration_idempotence_and_save_reopen(self):
        rig = fixture()
        resources = legacy(rig)
        initial, poses = content((rig,)), sampled_poses((rig,))
        old_records = records(rig, resources)
        identities = {item['label']: item['collection'].as_pointer() for item in resources}
        names = set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())
        result = manager.organize(bpy.context, rig)
        self.assertEqual(result['deleted'], 0)
        self.assertEqual(result['collections'], len(resources))
        self.assertEqual(len(result['renamed']), len(resources))
        self.assertEqual(records(rig, resources), old_records)
        self.assertEqual(content((rig,)), initial)
        self.assertEqual(sampled_poses((rig,)), poses)
        self.assertEqual((set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())), names)
        root = next(c for c in bpy.context.scene.collection.children if manager._owned(c, 'ROOT'))
        folder = next(c for c in root.children if c.get(manager.RIG_KEY) == rig)
        for item in resources:
            leaf = item['collection']
            self.assertEqual(leaf.as_pointer(), identities[item['label']])
            self.assertEqual(manager._parents(leaf), (folder,))
            self.assertEqual(leaf.users, 1)
            self.assertFalse(leaf.children)
            self.assertEqual(set(leaf.objects.keys()), item['objects'])
        organized = layout()
        self.assertEqual(manager.organize(bpy.context, rig)['renamed'], [])
        self.assertEqual(layout(), organized)
        name = rig.name
        with tempfile.TemporaryDirectory(prefix='cd-widget-hierarchy-') as folder_path:
            path = str(Path(folder_path) / 'grouped.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            rig = bpy.data.objects[name]
            self.assertEqual(layout(), organized)
            self.assertEqual(manager.organize(bpy.context, rig)['renamed'], [])
            self.assertEqual(content((rig,)), initial)
            self.assertEqual(sampled_poses((rig,)), poses)

    def test_mid_migration_failure_restores_names_links_records_and_containers(self):
        rig = fixture()
        resources = legacy(rig)
        before = layout()
        raw = {item['key']: rig.data[item['key']] for item in resources if item['key']}
        before_content = content((rig,))
        original, calls = manager._name, []
        def fail_second(leaf, name):
            original(leaf, name)
            calls.append(name)
            if len(calls) == 2:
                raise ValueError('Injected rename failure')
        manager._name = fail_second
        try:
            with self.assertRaisesRegex(ValueError, 'Injected rename failure'):
                manager.organize(bpy.context, rig)
        finally:
            manager._name = original
        self.assertEqual(layout(), before)
        self.assertEqual(content((rig,)), before_content)
        self.assertEqual({key: rig.data[key] for key in raw}, raw)
        manager._resources(rig)

    def test_foreign_membership_and_name_conflict_fail_before_scene_changes(self):
        rig = fixture()
        resources = legacy(rig)
        leaf = resources[0]['collection']
        mesh = bpy.data.meshes.new('Artist Geometry')
        artist = bpy.data.objects.new('Artist Geometry', mesh)
        leaf.objects.link(artist)
        before = layout()
        with self.assertRaisesRegex(ValueError, 'unregistered objects'):
            manager.organize(bpy.context, rig)
        self.assertEqual(layout(), before)
        leaf.objects.unlink(artist)
        occupied = bpy.data.collections.new(manager.ROOT_NAME)
        bpy.context.scene.collection.children.link(occupied)
        before = layout()
        with self.assertRaisesRegex(ValueError, 'artist collection'):
            manager.organize(bpy.context, rig)
        self.assertEqual(layout(), before)

    def test_renamed_foot_collection_removes_cleanly_and_empty_folders_are_pruned(self):
        rig, key, _ = build_leg('DIRECT_PREROLL', 'LEFT_LEG', toes=True)
        foot_controls.build(bpy.context, rig, key)
        legacy(rig)
        manager.organize(bpy.context, rig)
        record = foot_controls.get_record(rig, key)
        collection_name = record['widget_collection']
        foot_controls.remove(bpy.context, rig, key)
        self.assertNotIn(collection_name, bpy.data.collections)
        limb_ik._validate_inventory(rig)
        # Never prune an owned folder containing even one artist object.
        root = next(c for c in bpy.data.collections if manager._owned(c, 'ROOT'))
        kept = bpy.data.collections.new('Owned Empty Test')
        kept[manager.OWNER_KEY], kept[manager.ROLE_KEY] = manager.OWNER_VALUE, 'RIG'
        root.children.link(kept)
        obj = bpy.data.objects.new('Keep Artist Object', None)
        kept.objects.link(obj)
        manager.prune_empty(bpy.context)
        self.assertIn(kept.name, bpy.data.collections)
        kept.objects.unlink(obj)
        name = kept.name
        manager.prune_empty(bpy.context)
        self.assertNotIn(name, bpy.data.collections)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WidgetCollectionTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('WIDGET_COLLECTIONS_PASSED', result.testsRun, flush=True)
