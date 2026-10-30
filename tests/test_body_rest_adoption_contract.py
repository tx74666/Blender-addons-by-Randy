"""Proof separation and capability lifetime, without starting Blender."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'
PACKAGE = '_current_leg_rest_contract'


def load(name):
    fullname = PACKAGE + '.' + name
    spec = importlib.util.spec_from_file_location(fullname, SOURCE / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fullname] = mod
    spec.loader.exec_module(mod)
    return mod


def row():
    return {'parent': None, 'head': [0., 0., 0.], 'tail': [0., 1., 0.],
        'matrix': [1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.],
        'use_connect': False, 'use_deform': True, 'use_inherit_rotation': True,
        'inherit_scale': 'FULL', 'use_local_location': True, 'use_relative_parent': False}


class ProofContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before_modules = set(sys.modules)
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(SOURCE)]
        sys.modules[PACKAGE] = package
        fake_bpy = types.ModuleType('bpy')
        fake_bpy.types = types.SimpleNamespace(Action=type('Action', (), {}), Armature=type('Armature', (), {}))
        fake_math = types.ModuleType('mathutils')
        fake_math.Vector = object
        with patch.dict(sys.modules, {'bpy': fake_bpy, 'mathutils': fake_math}):
            cls.policy = load('body_rest_adoption_policy')
            cls.mod = load('body_rest_adoption')
        target = types.ModuleType(PACKAGE + '.animation_worklist_target')
        def names(value):
            if type(value) is not list or not value or value != sorted(set(value)):
                raise ValueError('Malformed names')
            return frozenset(value)
        target._validated_names = names
        ua = types.ModuleType(PACKAGE + '.unity_animation')
        def same(old, new):
            return (all(old[k] == new[k] for k in ('parent', 'use_deform', 'use_inherit_rotation',
                'inherit_scale', 'use_local_location', 'use_relative_parent'))
                and cls.policy._same_geometry(old, new))
        ua._same_current_rest_bone = same
        limb = types.ModuleType(PACKAGE + '.limb_ik');limb.OWNER_KEY = 'control_owner'
        skirt = types.ModuleType(PACKAGE + '.skirt_rig');skirt.OWNER_KEY = 'skirt_owner'
        region = types.ModuleType(PACKAGE + '.body_region');region.RECOVERY_OWNER_KEY = 'owner_revision'
        for name, value in [('animation_worklist_target',target),('unity_animation',ua),
                            ('limb_ik',limb),('skirt_rig',skirt),('body_region',region)]:
            sys.modules[PACKAGE+'.'+name] = value
            setattr(package,name,value)
        cls.region = region

    @classmethod
    def tearDownClass(cls):
        for name in tuple(sys.modules):
            if name == PACKAGE or name.startswith(PACKAGE+'.'):
                sys.modules.pop(name,None)

    def setUp(self):
        self.old = {'thigh.L': row()}
        self.motion = copy.deepcopy(self.old)
        self.rig = types.SimpleNamespace(data=types.SimpleNamespace(bones={'thigh.L': {}}))
        self.mod._ACTIVE = None

    def pair(self, names=None):
        return self.mod._historical_pair(self.rig,self.old,self.motion,
            ['thigh.L'] if names is None else names)

    def test_accepts_unchanged_historical_relationship(self):
        before = copy.deepcopy((self.old,self.motion))
        self.pair()
        self.assertEqual((self.old,self.motion),before)

    def test_rejects_well_formed_but_nonexistent_mapped_name(self):
        with self.assertRaisesRegex(ValueError,'mapped names'):
            self.pair(['not_in_either_snapshot'])

    def test_rejects_two_independently_valid_unrelated_snapshots(self):
        self.motion['thigh.L']['head'][2] = .01
        with self.assertRaisesRegex(ValueError,'historical motion changed'):
            self.pair()

    def test_rejects_changed_old_parent_relation(self):
        self.old['Hips'] = row();self.motion['Hips'] = row()
        self.motion['thigh.L']['parent'] = 'Hips'
        with self.assertRaisesRegex(ValueError,'historical motion changed'):
            self.pair()

    def test_rejects_unmapped_disconnect(self):
        self.old['other'] = row();self.motion['other'] = row()
        self.old['other']['use_connect'] = True
        with self.assertRaisesRegex(ValueError,'connected-bone'):
            self.pair()

    def test_accepts_only_mapped_unowned_disconnect(self):
        self.old['thigh.L']['use_connect'] = True
        self.pair()

    def test_rejects_generated_disconnect(self):
        self.old['thigh.L']['use_connect'] = True
        self.rig.data.bones['thigh.L']['control_owner'] = 'generated'
        with self.assertRaisesRegex(ValueError,'connected-bone'):
            self.pair()

    def test_rejects_reconnect(self):
        self.motion['thigh.L']['use_connect'] = True
        with self.assertRaisesRegex(ValueError,'connected-bone'):
            self.pair()

    def test_rejects_missing_bone(self):
        self.motion['other'] = row()
        with self.assertRaisesRegex(ValueError,'bone inventory'):
            self.pair()

    def test_rejects_nan_before_comparison(self):
        self.old['thigh.L']['matrix'][0] = float('nan')
        with self.assertRaises(ValueError):
            self.pair()

    def test_rejects_bool_as_coordinate_before_comparison(self):
        self.old['thigh.L']['head'][0] = True
        with self.assertRaises(ValueError):
            self.pair()

    def test_does_not_require_old_geometry_to_match_current_geometry(self):
        # Current geometry deliberately has no read interface here. It must
        # not be substituted for either immutable historical snapshot.
        self.rig.data.bones = object()
        self.pair()

    def test_no_tag_grants_current_controls_without_revision(self):
        rig = types.SimpleNamespace(data={})
        self.assertFalse(self.mod.has_revision(rig))

    def test_malformed_revision_does_not_dispatch(self):
        rig = types.SimpleNamespace(data={'owner_revision':'[]'})
        with self.assertRaisesRegex(ValueError,'invalid'):
            self.mod.has_revision(rig)

    def test_corrupted_audit_refuses_before_owner_read(self):
        rig = types.SimpleNamespace(data={'owner_revision':json.dumps({'mode':self.mod.MODE,'audit_sha256':'wrong'}),
            self.mod.AUDIT_KEY:'{}'})
        with patch.object(self.mod,'_hash',return_value='actual'), patch.object(self.mod,'_history') as history:
            with self.assertRaisesRegex(ValueError,'history changed'):
                self.mod.recovery_owners(rig)
            history.assert_not_called()

    def test_capability_is_for_exact_rig_and_data_only(self):
        self.mod._ACTIVE = {'rig':self.rig,'data':self.rig.data,'owners':{},'native_rest':{}}
        self.assertFalse(self.mod.active_owner(types.SimpleNamespace(data=self.rig.data)))
        self.rig.data = object()
        self.assertFalse(self.mod.active_owner(self.rig))

    def test_capability_always_clears_after_failure(self):
        with patch.object(self.mod,'_actual_binding'),patch.object(self.mod,'_history',return_value=(None,{'old':'unchanged'})),\
                patch.object(self.mod,'_native_rest',return_value={'new':'author'}):
            with self.assertRaisesRegex(RuntimeError,'injected'):
                with self.mod._admission(self.rig,{'old':'unchanged'},{'new':'author'}):
                    self.assertTrue(self.mod.active_owner(self.rig))
                    raise RuntimeError('injected')
        self.assertIsNone(self.mod._ACTIVE)

    def test_persistent_verification_cannot_borrow_capability(self):
        cap = {'rig':self.rig,'data':self.rig.data}
        self.mod._ACTIVE = cap
        def failed_persistent(rig):
            self.assertIsNone(self.mod._ACTIVE)
            return False
        with patch.object(self.mod,'has_revision',return_value=True),\
                patch.object(self.region,'verified_single_armature_user',failed_persistent,create=True):
            self.assertFalse(self.mod.verified_revision(self.rig))
        self.assertIs(self.mod._ACTIVE,cap)


if __name__ == '__main__':
    unittest.main()
