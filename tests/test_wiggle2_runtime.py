"""Shared solver ownership, isolated pause and transactional fault tests."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch


FIELDS = {'wiggle_enable': False, 'wiggle_head': False, 'wiggle_tail': False,
          'wiggle_mute': False, 'wiggle_head_mute': False, 'wiggle_tail_mute': False,
          'wiggle_stiff': 400.0, 'wiggle_damp': 8.0, 'wiggle_mass': 1.0,
          'wiggle_gravity': 0.25, 'wiggle_chain': True, 'wiggle_stretch': 0.0,
          'wiggle_wind_ob': None}


class Items(list):
    def __init__(self, touch=lambda: None, rows=False):
        super().__init__()
        self.touch, self.rows = touch, rows
    def add(self):
        self.touch()
        item = NS(name='')
        if self.rows:
            item.list = Items(self.touch)
        self.append(item)
        return item
    def get(self, name):
        return next((item for item in self if item.name == name), None)
    def remove(self, index):
        self.touch()
        del self[index]
    def move(self, source, target):
        self.insert(target, self.pop(source))


class Group:
    defaults = dict(iterations=2, loop=True, preroll=0, bake_overwrite=False,
                    bake_nla=False, is_rendering=False, is_preroll=False, reset=False,
                    lastframe=0, dt=0.0)
    def __init__(self, owner):
        object.__setattr__(self, 'owner', owner)
        object.__setattr__(self, 'raw', {})
        object.__setattr__(self, 'list', Items(self.touch, rows=True))
        object.__setattr__(self, 'history', [])
    def touch(self):
        self.owner.pg_exists = True
        self.raw['list'] = self.list
    def __getattr__(self, name):
        return self.raw.get(name, self.defaults.get(name))
    def __setattr__(self, name, value):
        self.owner.pg_exists = True
        self.history.append(('set', name))
        self.raw[name] = value
    def keys(self):
        return self.raw.keys()
    def __getitem__(self, name):
        return self.raw[name]
    def __setitem__(self, name, value):
        setattr(self, name, value)
    def __delitem__(self, name):
        self.history.append(('delete', name))
        del self.raw[name]


class Objects(dict):
    def __iter__(self):
        return iter(self.values())


class Owner:
    serial = 100
    def __init__(self, name, kind='ARMATURE'):
        object.__setattr__(self, 'raw', {})
        object.__setattr__(self, 'custom', {})
        object.__setattr__(self, 'writes', [])
        object.__setattr__(self, 'fault', None)
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'type', kind)
        object.__setattr__(self, 'pg_exists', False)
        object.__setattr__(self, 'group', Group(self))
        object.__setattr__(self, 'parent', None)
        object.__setattr__(self, 'pose', NS(bones=Objects()))
        Owner.serial += 1
        object.__setattr__(self, 'pointer', Owner.serial)
    def as_pointer(self):
        return self.pointer
    def get(self, name, default=None):
        return self.custom.get(name, default)
    def __getitem__(self, name):
        return self.custom[name]
    def __setitem__(self, name, value):
        self.custom[name] = value
    def __contains__(self, name):
        return name in self.custom
    def pop(self, name, default=None):
        return self.custom.pop(name, default)
    def __getattr__(self, name):
        if name == 'wiggle':
            return self.group
        if name in FIELDS or name in {'wiggle_freeze'}:
            return self.raw.get(name, FIELDS.get(name, False))
        raise AttributeError(name)
    def __setattr__(self, name, value):
        if name.startswith('wiggle_'):
            if self.fault == name:
                object.__setattr__(self, 'fault', None)
                raise RuntimeError('owned setter fault')
            self.writes.append((name, value))
            self.raw[name] = value
        else:
            object.__setattr__(self, name, value)
    def is_property_set(self, name):
        return self.pg_exists if name == 'wiggle' else name in self.raw
    def property_unset(self, name):
        if self.fault == name:
            self.fault = None
            raise RuntimeError('owned unset fault')
        if name == 'wiggle':
            self.pg_exists = False
            self.group = Group(self)
        else:
            self.writes.append((name, 'UNSET'))
            self.raw.pop(name, None)


def snapshot(owner):
    return dict(raw=dict(owner.raw), pg=owner.pg_exists,
                group={key: value for key, value in owner.group.raw.items() if key != 'list'},
                members=[(row.name, tuple(item.name for item in row.list)) for row in owner.group.list])


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.scene = Owner('Scene', 'SCENE')
        self.scene.frame_current = 23
        self.rig = Owner('Rig')
        self.other_rig = Owner('Peer Rig')
        self.a, self.b = Owner('HairBone', 'BONE'), Owner('SkirtBone', 'BONE')
        self.rig.pose.bones.update({self.a.name: self.a, self.b.name: self.b})
        self.hair, self.skirt = Owner('Hair', 'MESH'), Owner('Dress', 'MESH')
        self.scene.objects = Objects({obj.name: obj for obj in (self.rig, self.other_rig, self.hair, self.skirt)})
        self.context = NS(scene=self.scene, mode='OBJECT')
        self.bpy = NS(context=self.context, data=NS(objects=self.scene.objects),
                      types=NS(ID=Owner, PoseBone=NS(bl_rna=NS(properties=[NS(identifier=field) for field in FIELDS]))))
        path = Path(__file__).resolve().parents[1] / 'addons/character_designer/wiggle2_runtime.py'
        spec = importlib.util.spec_from_file_location('_wiggle_runtime_fixture', path)
        self.runtime = importlib.util.module_from_spec(spec)
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {'bpy': self.bpy}).start()
        spec.loader.exec_module(self.runtime)
        self.backend = ModuleType('_verified_wiggle2_fixture')
        self.backend.update_prop = lambda *args: None
        self.backend.reset_bone = lambda bone: setattr(bone.wiggle, 'position', (1.0, 2.0, 3.0))
        patch.dict(sys.modules, {self.backend.__name__: self.backend}).start()
        patch.object(self.runtime, '_prove_backend').start()
        patch.object(self.runtime, '_schema', return_value=('verified',)).start()
    def lease(self, component='HAIR', rig=None):
        bone, source = (self.a, self.hair) if component == 'HAIR' else (self.b, self.skirt)
        return self.runtime.acquire(self.context, component, source, rig or self.rig, (bone.name,), self.backend)
    def start(self, component='HAIR', rig=None):
        lease = self.lease(component, rig)
        self.runtime.configure(lease, {name: {'wiggle_stiff': 320.0} for name in lease['names']})
        return lease
    def test_same_rig_both_stop_orders_preserve_peer_and_first_last_enable(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                baseline = [snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)]
                hair, skirt = self.start(), self.start('SKIRT')
                first, last = (skirt, hair) if reverse else (hair, skirt)
                peer = snapshot(last['bones'][last['names'][0]])
                self.runtime.release(first)
                self.assertEqual(snapshot(last['bones'][last['names'][0]]), peer)
                self.assertTrue(self.scene.wiggle_enable)
                self.assertTrue(self.rig.wiggle_enable)
                self.runtime.validate(last)
                self.runtime.release(last)
                self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)], baseline)
                self.assertEqual(self.scene.frame_current, 23)
    def test_separate_rigs_restore_only_released_rig_enable(self):
        self.rig.pose.bones.pop(self.b.name)
        self.other_rig.pose.bones[self.b.name] = self.b
        hair, skirt = self.start(), self.start('SKIRT', self.other_rig)
        self.runtime.release(hair)
        self.assertFalse(self.rig.wiggle_enable)
        self.assertTrue(self.other_rig.wiggle_enable)
        self.assertTrue(self.scene.wiggle_enable)
        self.runtime.validate(skirt)
        self.runtime.release(skirt)
        self.assertFalse(self.scene.wiggle_enable)
    def test_part_pause_resume_changes_only_own_bone_and_never_rig_freeze(self):
        hair, skirt = self.start(), self.start('SKIRT')
        peer = snapshot(self.b)
        self.runtime.set_paused(hair, True)
        self.assertTrue(self.a.wiggle_mute)
        self.assertEqual(snapshot(self.b), peer)
        self.assertFalse(self.rig.wiggle_freeze)
        self.runtime.validate(skirt)
        self.runtime.set_paused(hair, False)
        self.assertFalse(self.a.wiggle_mute)
        self.assertEqual(snapshot(self.b), peer)
        self.assertEqual(self.scene.frame_current, 23)
    def test_join_does_not_reset_shared_clock_or_peer_runtime(self):
        self.start()
        self.scene.wiggle.lastframe = 18
        self.scene.wiggle.dt = 0.04
        peer = snapshot(self.a)
        self.start('SKIRT')
        self.assertEqual(self.scene.wiggle.lastframe, 18)
        self.assertEqual(self.scene.wiggle.dt, 0.04)
        self.assertEqual(snapshot(self.a), peer)
    def test_unknown_simulation_refuses_with_zero_owned_writes(self):
        self.b.wiggle_tail = True
        baseline = [snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)]
        with self.assertRaisesRegex(ValueError, 'unowned'):
            self.lease()
        self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)], baseline)
        self.assertFalse(self.runtime._LEASES)
    def test_overlap_and_ancestor_feedback_refuse_without_peer_change(self):
        hair = self.start()
        before = snapshot(self.a)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.runtime.acquire(self.context, 'SKIRT', self.skirt, self.rig, (self.a.name,), self.backend)
        self.b.parent = self.a
        with self.assertRaisesRegex(ValueError, 'ancestor'):
            self.lease('SKIRT')
        self.assertEqual(snapshot(self.a), before)
        self.runtime.validate(hair)
    def test_configure_seed_fault_rolls_back_only_new_owner(self):
        hair = self.start()
        peer = snapshot(self.a)
        skirt = self.lease('SKIRT')
        before = snapshot(self.b)
        self.backend.reset_bone = lambda bone: (_ for _ in ()).throw(RuntimeError('seed fault'))
        with self.assertRaisesRegex(RuntimeError, 'seed fault'):
            self.runtime.configure(skirt, {self.b.name: {'wiggle_mass': 3.0}})
        self.assertEqual(snapshot(self.a), peer)
        self.assertEqual(snapshot(self.b), before)
        self.runtime.release(skirt)
        self.runtime.validate(hair)
    def test_release_setter_fault_restores_lease_and_peer(self):
        hair, skirt = self.start(), self.start('SKIRT')
        before = [snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)]
        self.a.fault = 'wiggle_tail'
        with self.assertRaisesRegex(RuntimeError, 'unset fault'):
            self.runtime.release(hair)
        self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)], before)
        self.runtime.validate(hair)
        self.runtime.validate(skirt)
    def test_foreign_new_simulation_blocks_updates_but_own_release_preserves_it(self):
        hair = self.start()
        self.b.wiggle_tail = True
        self.runtime._list_add(self.scene, self.rig.name, (self.b.name,))
        foreign = snapshot(self.b)
        with self.assertRaisesRegex(ValueError, 'unowned'):
            self.runtime.set_paused(hair, True)
        self.runtime.release(hair)
        self.assertEqual(snapshot(self.b), foreign)
        self.assertTrue(self.scene.wiggle_enable)
        self.assertTrue(self.rig.wiggle_enable)
    def test_external_owned_flag_change_refuses_release_without_overwrite(self):
        hair = self.start()
        self.a.wiggle_head = True
        before = snapshot(self.a)
        with self.assertRaisesRegex(ValueError, 'flags changed'):
            self.runtime.release(hair)
        self.assertEqual(snapshot(self.a), before)
        self.assertIs(self.runtime._LEASES[hair['key']], hair)
    def test_history_forget_touches_no_native_reference(self):
        hair, skirt = self.start(), self.start('SKIRT')
        before = [snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)]
        self.runtime.forget('HAIR')
        self.assertNotIn(hair['key'], self.runtime._LEASES)
        self.assertIn(skirt['key'], self.runtime._LEASES)
        self.runtime.forget('SKIRT')
        self.assertFalse(self.runtime._SCENES)
        self.assertFalse(self.runtime._RIGS)
        self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)], before)
    def test_all_paused_detach_list_disable_native_and_resume_retains_peer(self):
        hair, skirt = self.start(), self.start('SKIRT')
        self.runtime.set_paused(hair, True)
        self.assertEqual(self.runtime._members(self.scene), {(self.rig.name, self.b.name)})
        self.assertTrue(self.scene.wiggle_enable)
        peer = snapshot(self.a)
        self.runtime.set_paused(skirt, True)
        self.assertFalse(self.runtime._members(self.scene))
        self.assertFalse(self.scene.wiggle_enable)
        self.assertFalse(self.rig.wiggle_enable)
        self.runtime.validate(hair)
        self.runtime.validate(skirt)
        self.assertEqual(snapshot(self.a), peer)
        self.scene.frame_current = 42
        self.runtime.set_paused(hair, False)
        self.assertEqual(self.runtime._members(self.scene), {(self.rig.name, self.a.name)})
        self.assertEqual(self.scene.wiggle.lastframe, 42)
        self.assertTrue(self.scene.wiggle_enable)
        self.assertTrue(self.b.wiggle_mute)
        self.runtime.validate(skirt)
    def test_pause_enable_setter_fault_restores_exact_peer_and_members(self):
        hair, skirt = self.start(), self.start('SKIRT')
        self.runtime.set_paused(hair, True)
        before = [snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)]
        self.scene.fault = 'wiggle_enable'
        with self.assertRaisesRegex(RuntimeError, 'setter fault'):
            self.runtime.set_paused(skirt, True)
        self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)], before)
        self.assertTrue(hair['paused'])
        self.assertFalse(skirt['paused'])
        self.runtime.validate(hair)
        self.runtime.validate(skirt)
    def test_resume_seed_fault_stays_detached_without_native_enable(self):
        hair = self.start()
        self.runtime.set_paused(hair, True)
        before = [snapshot(owner) for owner in (self.scene, self.rig, self.a)]
        self.backend.reset_bone = lambda bone: (_ for _ in ()).throw(RuntimeError('resume seed fault'))
        with self.assertRaisesRegex(RuntimeError, 'resume seed fault'):
            self.runtime.set_paused(hair, False)
        self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a)], before)
        self.runtime.validate(hair)
    def test_paused_stop_orders_restore_native_original_without_reconnecting_peer(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                baseline = [snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)]
                hair, skirt = self.start(), self.start('SKIRT')
                self.runtime.set_paused(hair, True)
                self.runtime.set_paused(skirt, True)
                first, last = (skirt, hair) if reverse else (hair, skirt)
                self.runtime.release(first)
                self.assertFalse(self.scene.wiggle_enable)
                self.assertFalse(self.runtime._members(self.scene))
                self.runtime.validate(last)
                self.runtime.release(last)
                self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)], baseline)
    def test_foreign_solver_appearing_while_paused_refuses_resume_and_survives_stop(self):
        hair = self.start()
        self.runtime.set_paused(hair, True)
        self.b.wiggle_tail = True
        self.runtime._list_add(self.scene, self.rig.name, (self.b.name,))
        self.scene.wiggle_enable = True
        self.rig.wiggle_enable = True
        before = [snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)]
        with self.assertRaises(ValueError):
            self.runtime.set_paused(hair, False)
        self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)], before)
        foreign = snapshot(self.b)
        self.runtime.release(hair)
        self.assertEqual(snapshot(self.b), foreign)
        self.assertTrue(self.scene.wiggle_enable)
        self.assertEqual(self.runtime._members(self.scene), {(self.rig.name, self.b.name)})
    def test_external_list_rebuild_cannot_reconnect_paused_owner_silently(self):
        hair = self.start()
        self.runtime.set_paused(hair, True)
        self.runtime._list_add(self.scene, self.rig.name, hair['names'])
        before = [snapshot(owner) for owner in (self.scene, self.rig, self.a)]
        with self.assertRaisesRegex(ValueError, 'list changed'):
            self.runtime.set_paused(hair, False)
        self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a)], before)
    def compatibility_fixture(self):
        for owner, kind, fields in ((self.scene, 'Scene', (*Group.defaults, 'list')),
                                   (self.rig, 'Object', ('list',)),
                                   (self.a, 'PoseBone', ('position',)),
                                   (self.b, 'PoseBone', ('position',))):
            owner.bl_rna = NS(identifier=kind)
            object.__setattr__(owner.group, 'bl_rna', NS(properties=[NS(identifier=name) for name in fields]))
        for owner, field in ((self.scene, 'syncing'), (self.rig, 'enable'),
                             (self.a, 'enable'), (self.b, 'enable')):
            owner.wiggle[field] = 0
            owner.group.history.clear()
    def test_exact_inactive_compatibility_is_preserved_without_rewriting(self):
        self.compatibility_fixture()
        before = [snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)]
        hair, skirt = self.start(), self.start('SKIRT')
        for lease in (hair, skirt):
            self.runtime.set_paused(lease, True)
        for lease in (hair, skirt):
            self.runtime.set_paused(lease, False)
        self.runtime.release(hair)
        self.runtime.release(skirt)
        self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a, self.b)], before)
        for owner in (self.scene, self.rig, self.a, self.b):
            self.assertFalse(any(key in {'syncing', 'enable'} for action, key in owner.group.history))
    def test_only_exact_owner_key_and_int_zero_compatibility_is_allowed(self):
        self.compatibility_fixture()
        for owner, key in ((self.scene, 'syncing'), (self.rig, 'enable'), (self.a, 'enable')):
            for bad in (1, -1, False, 0.0, '0', None):
                with self.subTest(owner=owner.name, bad=bad):
                    owner.wiggle[key] = bad
                    before = snapshot(owner)
                    with self.assertRaisesRegex(ValueError, 'Custom Wiggle'):
                        self.runtime._group(owner)
                    self.assertEqual(snapshot(owner), before)
                    owner.wiggle[key] = 0
        self.scene.wiggle['enable'] = 0
        with self.assertRaisesRegex(ValueError, 'Custom Wiggle'):
            self.lease()
        self.assertFalse(self.runtime._LEASES)
    def test_changed_or_new_compatibility_during_lease_refuses_before_write(self):
        self.compatibility_fixture()
        hair = self.start()
        for value in (1, False, 0.0):
            with self.subTest(value=value):
                self.a.wiggle['enable'] = value
                before = [snapshot(owner) for owner in (self.scene, self.rig, self.a)]
                with self.assertRaisesRegex(ValueError, 'Custom Wiggle'):
                    self.runtime.release(hair)
                self.assertEqual([snapshot(owner) for owner in (self.scene, self.rig, self.a)], before)
                self.a.wiggle['enable'] = 0
        del self.a.wiggle['enable']
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            self.runtime.set_paused(hair, True)
        self.assertTrue(hair['active'])
        self.assertFalse(hair['paused'])
        self.a.wiggle['enable'] = 0
        self.runtime.release(hair)
        del self.a.wiggle['enable']
        hair = self.start()
        self.a.wiggle['enable'] = 0
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            self.runtime.set_paused(hair, True)


if __name__ == '__main__':
    unittest.main()
