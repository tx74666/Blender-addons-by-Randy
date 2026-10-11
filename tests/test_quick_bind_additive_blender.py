"""Native rebind after unrelated deform-bone additions, preserving first backup.

Run: blender --background --factory-startup --python-exit-code 1
     --python tests/test_quick_bind_additive_blender.py
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_quick_bind_rebind_blender as base

service = base.service
EXTRAS = ('Hair Extra', 'SK_Dress DEF Extra')


def groups(obj):
    return {state['name']: state for state in base._capture_vertex_groups(obj)}


def rig_state(rig):
    return (
        rig.as_pointer(), rig.data.as_pointer(), rig.data.pose_position,
        tuple(tuple(row) for row in rig.matrix_world),
        tuple((bone.name, bone.use_deform, bone.parent.name if bone.parent else None,
               tuple(tuple(row) for row in bone.matrix_local), bone.head_radius,
               bone.tail_radius, bone.envelope_distance)
              for bone in rig.data.bones),
        tuple((bone.name, bone.rotation_mode, tuple(bone.location),
               tuple(bone.rotation_quaternion), tuple(bone.rotation_euler),
               tuple(bone.rotation_axis_angle), tuple(bone.scale),
               tuple(tuple(row) for row in bone.matrix_basis))
              for bone in rig.pose.bones),
    )


def object_state(obj):
    return (base.geometry_state(obj), base._capture_vertex_groups(obj),
            base.modifier_state(obj), obj.vertex_groups.active_index,
            obj.data.as_pointer(), tuple(mod.as_pointer() for mod in obj.modifiers),
            obj.get(service.BACKUP_KEY), obj.get(service.RIG_KEY),
            obj.parent_type, tuple(tuple(row) for row in obj.matrix_parent_inverse))


def add_extras(target, rig, body=None):
    active = bpy.context.view_layer.objects.active
    selected = tuple(bpy.context.selected_objects)
    for obj in selected:
        obj.select_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    # Inside the AUTO sphere: enabling these would let them compete for weights.
    for index, name in enumerate(EXTRAS):
        bone = rig.data.edit_bones.new(name)
        x = .3 if index == 0 else -.3
        bone.head, bone.tail = (x, 0, -.4), (x, 0, .4)
        bone.use_deform = True
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.select_set(False)
    for obj in selected:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active
    for obj in (target, body):
        if obj is None:
            continue
        # Exercise both explicit zero memberships and a genuinely empty group.
        zero = obj.vertex_groups.new(name=EXTRAS[0])
        zero.add((0,), 0.0, 'REPLACE')
        zero.lock_weight = True
        obj.vertex_groups.new(name=EXTRAS[1]).lock_weight = True
        artist = obj.vertex_groups.new(name='Later Artist Mask')
        artist.add((0,), .37, 'REPLACE')
        artist.lock_weight = True
    main = next(bone for bone in rig.pose.bones if bone.name not in EXTRAS)
    main.rotation_mode = 'QUATERNION'
    main.rotation_quaternion = (.995004165, .099833417, 0, 0)
    main.location = (.02, -.03, .01)
    bpy.context.view_layer.update()


def clear_objects():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def make_rig(names):
    rig = bpy.data.objects.new('Rig', bpy.data.armatures.new('Rig'))
    bpy.context.scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    for index, name in enumerate(names):
        bone = rig.data.edit_bones.new(name)
        bone.head, bone.tail = (index, 0, -.5), (index, 0, .5)
    control = rig.data.edit_bones.new('CTRL')
    control.head, control.tail = (0, 0, 0), (0, 1, 0)
    control.use_deform = False
    bpy.ops.object.mode_set(mode='OBJECT')
    return rig


def original_topology_fixture():
    clear_objects()
    rig = make_rig(('A', 'B'))
    body = base.mesh('Body', [(0, 0, 0), (2, 0, 0), (0, 2, 0)], [(0, 1, 2)])
    base.assign(body, 'A', (1, 0, .5))
    base.assign(body, 'B', (0, 1, .5))
    body.modifiers.new('Body Armature', 'ARMATURE').object = rig
    target = base.mesh('Shoes', [(.25, .25, .1), (1.25, .25, .1),
                                 (1.25, .75, .1), (.25, .75, .1)],
                       [(0, 1, 2), (0, 2, 3)])
    base.assign(target, 'A', (.2,) * 4)
    base.assign(target, 'ArtistMask', (.1, .2, .3, .4), lock=True)
    original_groups = groups(target)
    modifier = target.modifiers.new('Existing Armature', 'ARMATURE')
    modifier.object = rig
    modifier.use_vertex_groups = False
    modifier.use_bone_envelopes = True
    rig.select_set(False)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    service.bind_weights(bpy.context, target, rig, body=body)
    return target, body, rig, original_groups


class QuickBindAdditiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.character_designer.register()

    def assert_no_extra_weights(self, target):
        indices = {group.index for group in target.vertex_groups if group.name in EXTRAS}
        self.assertFalse(any(item.group in indices and item.weight > 0.0
                             for vertex in target.data.vertices for item in vertex.groups))

    def assert_refused_unchanged(self, target, body, rig, *, mode='TRANSFER', requested_rig=None):
        before = object_state(target), object_state(body), rig_state(rig)
        selection, datablocks = base.selection_state(), base.datablock_state()
        with patch.object(service, '_temporary_copy', side_effect=AssertionError('solver was reached')) as temporary_copy:
            with self.assertRaises(service.QuickBindError):
                service.bind_weights(bpy.context, target, requested_rig or rig, body=body, mode=mode)
            temporary_copy.assert_not_called()
        self.assertEqual((object_state(target), object_state(body), rig_state(rig)), before)
        self.assertEqual(base.selection_state(), selection)
        self.assertEqual(base.datablock_state(), datablocks)

    def test_transfer_added_bones_and_changed_topology_preserve_first_backup(self):
        for change in ('vertex_count', 'connectivity'):
            with self.subTest(change=change):
                target, body, rig, baseline = base.changed_fixture(change)
                add_extras(target, rig, body)
                geometry, modifiers = base.geometry_state(target), base.modifier_state(target)
                identity = target.data.as_pointer(), tuple(mod.as_pointer() for mod in target.modifiers)
                active_group = target.vertex_groups.active_index
                untracked = {name: state for name, state in groups(target).items() if name not in ('A', 'B')}
                source, native = object_state(body), rig_state(rig)
                selection, datablocks = base.selection_state(), base.datablock_state()
                for _ in range(2):
                    result = service.bind_weights(bpy.context, target, rig, body=body)
                    self.assertFalse(result['previous_weights_topology_matches'])
                    for vertex in target.data.vertices:
                        expected = 1 - vertex.co.x / 2 - vertex.co.y / 4
                        self.assertAlmostEqual(target.vertex_groups['A'].weight(vertex.index), expected, places=5)
                        self.assertAlmostEqual(target.vertex_groups['B'].weight(vertex.index), 1 - expected, places=5)
                    self.assert_no_extra_weights(target)
                    self.assertEqual({name: groups(target)[name] for name in untracked}, untracked)
                    self.assertEqual(base.geometry_state(target), geometry)
                    self.assertEqual(base.modifier_state(target), modifiers)
                    self.assertEqual((target.data.as_pointer(), tuple(mod.as_pointer() for mod in target.modifiers)), identity)
                    self.assertEqual(target.vertex_groups.active_index, active_group)
                    self.assertEqual(target[service.BACKUP_KEY].encode('utf-8'), baseline)
                    self.assertEqual(target[service.RIG_KEY], rig)
                    self.assertEqual(json.loads(target[service.BACKUP_KEY])['names'], ['A', 'B'])
                    self.assertEqual(object_state(body), source)
                    self.assertEqual(rig_state(rig), native)
                    self.assertEqual(base.selection_state(), selection)
                    self.assertEqual(base.datablock_state(), datablocks)
                before_restore = object_state(target)
                with self.assertRaisesRegex(service.QuickBindError, 'topology'):
                    service.restore_binding(bpy.context, target)
                self.assertEqual(object_state(target), before_restore)

    def test_auto_only_temporary_rig_disables_added_deform_bones(self):
        clear_objects()
        rig = make_rig(('Foot',))
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=1)
        target = bpy.context.object
        target.name = 'Shoes'
        base.assign(target, 'ArtistMask', (.25,) * len(target.data.vertices), lock=True)
        service.bind_weights(bpy.context, target, rig, mode='AUTO')
        baseline = target[service.BACKUP_KEY].encode('utf-8')
        add_extras(target, rig)
        before, native = object_state(target), rig_state(rig)
        selection, datablocks = base.selection_state(), base.datablock_state()
        observed = []
        real_auto = service._run_auto

        def inspect_auto(context, mesh, temporary_rig):
            self.assertNotEqual(temporary_rig.as_pointer(), rig.as_pointer())
            self.assertNotEqual(temporary_rig.data.as_pointer(), rig.data.as_pointer())
            observed.append({bone.name: bone.use_deform for bone in temporary_rig.data.bones})
            self.assertEqual(observed[-1], {'Foot': True, 'CTRL': False,
                                          EXTRAS[0]: False, EXTRAS[1]: False})
            return real_auto(context, mesh, temporary_rig)

        with patch.object(service, '_run_auto', side_effect=inspect_auto):
            result = service.bind_weights(bpy.context, target, rig, mode='AUTO')
        self.assertEqual(len(observed), 1)
        self.assertEqual(result['group_count'], 1)
        for vertex in target.data.vertices:
            self.assertAlmostEqual(target.vertex_groups['Foot'].weight(vertex.index), 1.0, places=6)
        self.assert_no_extra_weights(target)
        self.assertEqual(object_state(target), before)
        self.assertEqual(rig_state(rig), native)
        self.assertEqual(target[service.BACKUP_KEY].encode('utf-8'), baseline)
        self.assertEqual(base.selection_state(), selection)
        self.assertEqual(base.datablock_state(), datablocks)

    def test_added_bone_positive_weights_refuse_before_solving(self):
        for weighted_object, mode in (('target', 'TRANSFER'), ('body', 'TRANSFER'), ('target', 'AUTO')):
            with self.subTest(weighted_object=weighted_object, mode=mode):
                target, body, rig, _ = base.changed_fixture('connectivity')
                add_extras(target, rig, body)
                obj = target if weighted_object == 'target' else body
                obj.vertex_groups[EXTRAS[0]].add((0,), .5, 'REPLACE')
                self.assert_refused_unchanged(target, body, rig, mode=mode)

    def test_saved_bone_removal_rename_and_foreign_rig_refuse(self):
        for change in ('remove', 'rename', 'foreign'):
            with self.subTest(change=change):
                target, body, rig, _ = base.changed_fixture('connectivity')
                add_extras(target, rig, body)
                requested = rig
                if change == 'remove':
                    target.select_set(False)
                    rig.select_set(True)
                    bpy.context.view_layer.objects.active = rig
                    bpy.ops.object.mode_set(mode='EDIT')
                    rig.data.edit_bones.remove(rig.data.edit_bones['A'])
                    bpy.ops.object.mode_set(mode='OBJECT')
                    rig.select_set(False)
                    target.select_set(True)
                    bpy.context.view_layer.objects.active = target
                elif change == 'rename':
                    rig.data.bones['A'].name = 'Renamed A'
                else:
                    requested = rig.copy()
                    requested.data = rig.data.copy()
                    requested.name = 'Foreign Rig'
                    bpy.context.scene.collection.objects.link(requested)
                bpy.context.view_layer.update()
                self.assert_refused_unchanged(target, body, rig, requested_rig=requested)

    def test_saved_deform_group_lock_still_refuses(self):
        target, body, rig, _ = base.changed_fixture('connectivity')
        add_extras(target, rig, body)
        target.vertex_groups['A'].lock_weight = True
        self.assert_refused_unchanged(target, body, rig)

    def test_original_topology_restore_has_no_added_positive_weights(self):
        target, body, rig, original = original_topology_fixture()
        baseline = target[service.BACKUP_KEY].encode('utf-8')
        add_extras(target, rig, body)
        untracked = {name: state for name, state in groups(target).items() if name not in ('A', 'B')}
        source, native = object_state(body), rig_state(rig)
        for _ in range(2):
            result = service.bind_weights(bpy.context, target, rig, body=body)
            self.assertTrue(result['previous_weights_topology_matches'])
            self.assertEqual(target[service.BACKUP_KEY].encode('utf-8'), baseline)
            self.assert_no_extra_weights(target)
        service.restore_binding(bpy.context, target)
        restored = groups(target)
        self.assertEqual(restored['A'], original['A'])
        self.assertNotIn('B', restored)
        # B was created by Quick Bind and its removal shifts later group indices.
        for name, state in untracked.items():
            self.assertEqual(restored[name]['lock_weight'], state['lock_weight'])
            self.assertEqual(restored[name]['weights'], state['weights'])
        self.assert_no_extra_weights(target)
        self.assertFalse(service.has_binding_backup(target))
        self.assertIsNone(target.parent)
        self.assertFalse(target.modifiers[0].use_vertex_groups)
        self.assertTrue(target.modifiers[0].use_bone_envelopes)
        self.assertEqual(object_state(body), source)
        self.assertEqual(rig_state(rig), native)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(QuickBindAdditiveTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
    print('QUICK_BIND_ADDITIVE_OK')
