"""Rebind current topology while retaining the first, strictly guarded backup.

Run: blender --background --factory-startup --python-exit-code 1
     --python tests/test_quick_bind_rebind_blender.py
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
import character_designer
from character_designer import character_setup, quick_bind as service
from character_designer.selected_bone_weights import _capture_vertex_groups


def mesh(name, coords, faces):
    data = bpy.data.meshes.new(name)
    data.from_pydata(coords, [], faces)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def assign(obj, name, values, *, lock=False):
    group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
    for index, value in enumerate(values):
        group.add((index,), value, 'REPLACE')
    group.lock_weight = lock


def geometry_state(obj):
    keys = obj.data.shape_keys
    return (
        tuple(tuple(vertex.co) for vertex in obj.data.vertices),
        tuple(tuple(edge.vertices) for edge in obj.data.edges),
        tuple(tuple(face.vertices) for face in obj.data.polygons),
        tuple((layer.name, tuple(tuple(loop.uv) for loop in layer.data))
              for layer in obj.data.uv_layers),
        tuple((key.name, key.value, key.mute, tuple(tuple(v.co) for v in key.data))
              for key in keys.key_blocks) if keys else (),
        tuple(tuple(row) for row in obj.matrix_world),
        obj.parent.name if obj.parent else None,
    )


def modifier_state(obj):
    return tuple((mod.name, mod.type, mod.persistent_uid, mod.show_viewport,
                  mod.show_render,
                  (mod.object.name if mod.object else None, mod.vertex_group,
                   mod.use_vertex_groups, mod.use_bone_envelopes,
                   mod.use_deform_preserve_volume) if mod.type == 'ARMATURE' else None)
                 for mod in obj.modifiers)


def target_state(obj):
    return (geometry_state(obj), _capture_vertex_groups(obj), modifier_state(obj),
            obj.vertex_groups.active_index, obj[service.BACKUP_KEY].encode('utf-8'),
            obj[service.RIG_KEY].name, obj['artist_note'])


def selection_state():
    return (bpy.context.view_layer.objects.active.name,
            tuple(sorted(obj.name for obj in bpy.context.selected_objects)))


def datablock_state():
    return tuple(tuple(sorted(item.name for item in collection)) for collection in
                 (bpy.data.objects, bpy.data.meshes, bpy.data.armatures, bpy.data.shape_keys))


def changed_fixture(change):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    rig = bpy.data.objects.new('Rig', bpy.data.armatures.new('Rig'))
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for index, name in enumerate(('A', 'B')):
        bone = rig.data.edit_bones.new(name)
        bone.head, bone.tail = (index, 0, 0), (index, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    body = mesh('Body', [(0, 0, 0), (2, 0, 0), (0, 2, 0)], [(0, 1, 2)])
    # Native interpolation gives A = 1 - x / 2 - y / 4, B = 1 - A.
    assign(body, 'A', (1, 0, .5))
    assign(body, 'B', (0, 1, .5))
    body.modifiers.new('Body Armature', 'ARMATURE').object = rig
    target = mesh('Stocking', [(.25, .25, .1), (1.25, .25, .1),
                               (1.25, .75, .1), (.25, .75, .1)],
                  [(0, 1, 2), (0, 2, 3)])
    assign(target, 'A', (.2,) * 4)
    assign(target, 'B', (.8,) * 4)
    assign(target, 'ArtistMask', (.1, .2, .3, .4), lock=True)
    target.modifiers.new('Artist Armature', 'ARMATURE').object = rig
    state = character_setup.settings(bpy.context)
    state.rig, state.body = rig, body
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    initial = service.bind_weights(bpy.context, target, rig, body=body)
    assert initial['previous_weights_topology_matches']
    baseline = target[service.BACKUP_KEY].encode('utf-8')
    topology = service._topology_signature(target)
    old_counts = (len(target.data.vertices), len(target.data.edges), len(target.data.polygons))

    bm = bmesh.new()
    try:
        bm.from_mesh(target.data)
        bm.verts.ensure_lookup_table()
        if change == 'vertex_count':
            bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=1, use_grid_fill=True)
        elif change == 'connectivity':
            diagonal = bm.edges.get((bm.verts[0], bm.verts[2]))
            for face in tuple(bm.faces):
                bm.faces.remove(face)
            bm.edges.remove(diagonal)
            bm.faces.new((bm.verts[0], bm.verts[1], bm.verts[3]))
            bm.faces.new((bm.verts[1], bm.verts[2], bm.verts[3]))
        else:
            raise AssertionError(change)
        bm.to_mesh(target.data)
    finally:
        bm.free()
    target.data.update()
    new_counts = (len(target.data.vertices), len(target.data.edges), len(target.data.polygons))
    assert service._topology_signature(target) != topology
    if change == 'vertex_count':
        assert new_counts[0] > old_counts[0]
    else:
        assert new_counts == old_counts, 'Connectivity regression must keep every element count'
        assert any(set(edge.vertices) == {1, 3} for edge in target.data.edges)

    count = len(target.data.vertices)
    # Deliberately edited current weights distinguish recalculation from a no-op
    # or restoring the first backup; failure must restore these current values.
    assign(target, 'A', (.13,) * count)
    assign(target, 'B', (.87,) * count)
    assign(target, 'ArtistMask', ((index + 1) / (count + 1) for index in range(count)), lock=True)
    target.vertex_groups.active_index = target.vertex_groups['ArtistMask'].index
    target.data.uv_layers.new(name='Artist UV')
    for index, loop in enumerate(target.data.uv_layers.active.data):
        loop.uv = (index / 31, .7)
    target.shape_key_add(name='Basis')
    key = target.shape_key_add(name='Artist Shape')
    key.data[0].co.x += .35
    key.data[0].co.z += .2
    key.value = .65
    modifier = target.modifiers[0]
    modifier.name = 'Artist renamed Armature'
    modifier.use_deform_preserve_volume = True
    modifier.show_render = False
    smooth = target.modifiers.new('Artist Subdivision', 'SUBSURF')
    smooth.levels = 0
    target['artist_note'] = 'Preserve current topology and artist edits'
    bpy.context.view_layer.update()
    return target, body, rig, baseline


class QuickBindRebindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def assert_native_weights(self, target):
        for vertex in target.data.vertices:
            expected = 1 - vertex.co.x / 2 - vertex.co.y / 4
            self.assertAlmostEqual(target.vertex_groups['A'].weight(vertex.index), expected, places=5)
            self.assertAlmostEqual(target.vertex_groups['B'].weight(vertex.index), 1 - expected, places=5)

    def test_native_rebind_changed_topology_preserves_current_artist_data_and_first_backup(self):
        for change in ('vertex_count', 'connectivity'):
            with self.subTest(change=change):
                target, body, rig, baseline = changed_fixture(change)
                geometry = geometry_state(target)
                modifiers = modifier_state(target)
                identities = target.data.as_pointer(), tuple(mod.as_pointer() for mod in target.modifiers)
                mask = _capture_vertex_groups(target)[2]
                source = geometry_state(body), _capture_vertex_groups(body), modifier_state(body)
                selection, datablocks = selection_state(), datablock_state()
                for _ in range(2):
                    result = service.bind_weights(bpy.context, target, rig, body=body)
                    self.assertEqual(result['vertex_count'], len(target.data.vertices))
                    self.assertFalse(result['previous_weights_topology_matches'])
                    self.assert_native_weights(target)
                    self.assertEqual(geometry_state(target), geometry)
                    self.assertEqual(modifier_state(target), modifiers)
                    self.assertEqual((target.data.as_pointer(), tuple(mod.as_pointer() for mod in target.modifiers)), identities)
                    self.assertEqual(_capture_vertex_groups(target)[2], mask)
                    self.assertEqual(target.vertex_groups.active_index, 2)
                    self.assertEqual(target[service.BACKUP_KEY].encode('utf-8'), baseline)
                    self.assertEqual(target[service.RIG_KEY], rig)
                    self.assertEqual((geometry_state(body), _capture_vertex_groups(body), modifier_state(body)), source)
                    self.assertEqual(selection_state(), selection)
                    self.assertEqual(datablock_state(), datablocks)
                before_restore = target_state(target)
                with self.assertRaisesRegex(service.QuickBindError, 'topology'):
                    service.restore_binding(bpy.context, target)
                self.assertEqual(target_state(target), before_restore)
                self.assertEqual(selection_state(), selection)
                self.assertEqual(datablock_state(), datablocks)

    def test_changed_topology_solver_and_partial_write_failures_roll_back_current_state(self):
        for change in ('vertex_count', 'connectivity'):
            for fault in ('transfer', 'automatic', 'write'):
                with self.subTest(change=change, fault=fault):
                    target, body, rig, baseline = changed_fixture(change)
                    modifier = target.modifiers[0]
                    modifier.use_vertex_groups = False
                    modifier.use_bone_envelopes = True
                    before, selection, datablocks = target_state(target), selection_state(), datablock_state()
                    identities = target.data.as_pointer(), tuple(mod.as_pointer() for mod in target.modifiers)

                    def fail(*args):
                        if fault == 'write':
                            obj = args[0]
                            obj.vertex_groups['A'].add((0,), .99, 'REPLACE')
                            obj.vertex_groups['B'].remove(tuple(range(len(obj.data.vertices))))
                            obj.vertex_groups.new(name='Injected partial group').add((0,), .5, 'REPLACE')
                        raise RuntimeError('injected ' + fault + ' failure')

                    function = {'transfer': '_interpolate', 'automatic': '_run_auto', 'write': '_write_weights'}[fault]
                    with patch.object(service, function, side_effect=fail):
                        with self.assertRaisesRegex(service.QuickBindError, 'injected ' + fault + ' failure'):
                            service.bind_weights(bpy.context, target, rig, body=body,
                                                 mode='AUTO' if fault == 'automatic' else 'TRANSFER')
                    self.assertEqual(target_state(target), before)
                    self.assertEqual((target.data.as_pointer(), tuple(mod.as_pointer() for mod in target.modifiers)), identities)
                    self.assertEqual(target[service.BACKUP_KEY].encode('utf-8'), baseline)
                    self.assertEqual(selection_state(), selection)
                    self.assertEqual(datablock_state(), datablocks)

    def test_operator_rebind_undo_redo_keeps_topology_and_first_backup(self):
        for change in ('vertex_count', 'connectivity'):
            with self.subTest(change=change):
                target, body, rig, baseline = changed_fixture(change)
                before = target_state(target)
                bpy.context.preferences.edit.use_global_undo = True
                bpy.ops.ed.undo_push(message='Before rebind on edited topology')
                self.assertEqual(bpy.ops.character_designer.quick_bind(mode='TRANSFER'), {'FINISHED'})
                self.assertIn('Previous Weights kept', character_setup.settings(bpy.context).last_message)
                bpy.ops.ed.undo_push(message='After rebind on edited topology')
                self.assert_native_weights(target)
                after = target_state(target)
                self.assertEqual(target[service.BACKUP_KEY].encode('utf-8'), baseline)
                self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
                target = bpy.data.objects['Stocking']
                self.assertEqual(target_state(target), before)
                self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
                target = bpy.data.objects['Stocking']
                self.assertEqual(target_state(target), after)
                self.assert_native_weights(target)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(QuickBindRebindTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
    print('QUICK_BIND_REBIND_OK')
