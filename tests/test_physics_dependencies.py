"""Explicit dependency registration, source selection and cleanup evidence."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_physics_dependency_fixture',
    ROOT / 'addons/character_designer/physics_dependencies.py')
dependency = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependency)
native_registration_state = dependency._registration_state


class DependencyTests(unittest.TestCase):
    def setUp(self):
        dependency._CACHE.clear()
        dependency._OWNED_REGISTRATIONS.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enabled, self.modules, self.calls = set(), [], []
        self.native = {'schema': [], 'classes': [], 'handlers': {}, 'wiggle_named_handlers': []}
        self.adapter = SimpleNamespace(BACKEND_SHA256='', _HASHES={},
            _BACKEND_CACHE={'available': False}, _BACKEND_CACHE_TIME=0.0,
            _AVAILABLE_CACHE={'available': False}, _AVAILABLE_CACHE_TIME=0.0,
            _backend=lambda: self.modules[0])
        self.addons = ModuleType('addon_utils')
        self.addons.modules = lambda **kwargs: self.modules
        self.addons.check = lambda name: (False, name in self.enabled)
        self.addons.enable = self.enable
        self.addons.disable = self.disable
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {'addon_utils': self.addons}).start()
        patch.object(dependency, '_adapter', return_value=self.adapter).start()
        patch.object(dependency, '_registration_state', side_effect=lambda *args: dict(self.native)).start()

    def installed_skirt(self, name='wiggle_2'):
        path = self.root / (name + '.py')
        path.write_bytes(b'# Verified source fixture\ndef register():\n    pass\n')
        module = ModuleType(name)
        module.__file__ = str(path)
        module.bl_info = {'name': 'Wiggle 2', 'version': (2, 2, 4)}
        self.adapter.BACKEND_SHA256 = hashlib.sha256(path.read_bytes()).hexdigest()
        self.modules.append(module)
        patch.dict(sys.modules, {name: module}).start()
        return module

    def mark_enabled(self, name='wiggle_2'):
        self.enabled.add(name)
        self.native['schema'] = [(owner, 'wiggle', 'POINTER', 12345 + index)
                                for index, owner in enumerate(('Scene', 'Object', 'PoseBone'))]

    def enable(self, name, **kwargs):
        self.calls.append(('enable', name, kwargs))
        self.mark_enabled(name)
        return next(module for module in self.modules if module.__name__ == name)

    def disable(self, name, **kwargs):
        self.calls.append(('disable', name, kwargs))
        was_enabled = name in self.enabled
        self.enabled.discard(name)
        if was_enabled:
            self.native['schema'] = []

    def test_uncached_drawing_does_not_scan_or_hash(self):
        self.addons.modules = lambda **kwargs: (_ for _ in ()).throw(AssertionError('draw scanned'))
        with patch.object(dependency, '_source_proof', side_effect=AssertionError('draw hashed')):
            info = dependency.status('SKIRT')
        self.assertEqual(info['state'], 'UNCHECKED')
        self.assertIsNone(info['installed'])
        self.assertTrue(info['enable_available'])

    def test_legacy_feature_cache_is_not_reused_as_wiggle2_proof(self):
        dependency._CACHE['HAIR'] = dict(state='READY', label='Wiggle Bones 1.1.2', details=[])
        self.assertEqual(dependency.status('HAIR')['state'], 'UNCHECKED')

    def test_hair_and_skirt_use_one_installed_backend(self):
        self.installed_skirt()
        info = dependency.status('HAIR', refresh=True)
        self.assertEqual(info['state'], 'DISABLED')
        self.assertTrue(info['enable_available'])
        self.assertEqual(info['backend'], 'WIGGLE2')
        self.assertEqual(dependency.status('SKIRT')['state'], 'DISABLED')
        self.assertFalse(self.calls)

    def test_legacy_hair_does_not_satisfy_shared_wiggle2_requirement(self):
        directory = self.root / 'legacy'
        directory.mkdir()
        (directory / 'blender_manifest.toml').write_text('id = "wiggle_bones"\nversion = "1.1.2"\n')
        module = ModuleType('legacy_wiggle')
        module.__file__ = str(directory / '__init__.py')
        self.modules.append(module)
        info = dependency.status('HAIR', refresh=True)
        self.assertEqual(info['state'], 'MISSING')
        self.assertIn('Wiggle 2 2.2.4', info['message'])
        self.assertFalse(self.calls)

    def test_enabled_legacy_conflicts_with_wiggle2_without_disabling_it(self):
        self.installed_skirt()
        directory = self.root / 'legacy'
        directory.mkdir()
        (directory / 'blender_manifest.toml').write_text('id = "wiggle_bones"\nversion = "1.1.2"\n')
        module = ModuleType('legacy_wiggle')
        module.__file__ = str(directory / '__init__.py')
        self.modules.append(module)
        self.enabled.add(module.__name__)
        with self.assertRaisesRegex(ValueError, 'legacy Wiggle'):
            dependency.enable('HAIR')
        self.assertFalse(self.calls)
        self.assertIn(module.__name__, self.enabled)

    def test_enabling_hair_then_skirt_registers_once_and_shares_ready_proof(self):
        self.installed_skirt()
        dependency.enable('HAIR')
        self.assertEqual(dependency.status('SKIRT')['state'], 'READY')
        dependency.enable('SKIRT')
        self.assertEqual([call[0] for call in self.calls], ['enable'])
        self.assertEqual(dependency.status('HAIR')['component'], 'HAIR')
        self.assertEqual(dependency.status('SKIRT')['component'], 'SKIRT')

    def test_one_click_enables_exact_installed_module_without_saving(self):
        self.installed_skirt('installed_wiggle_2')
        info = dependency.enable('SKIRT')
        self.assertEqual(info['state'], 'READY')
        self.assertEqual(self.calls[0][1], 'installed_wiggle_2')
        self.assertIs(self.calls[0][2]['default_set'], False)
        self.assertIs(self.calls[0][2]['persistent'], False)
        self.assertIsNone(self.adapter._BACKEND_CACHE)
        self.assertFalse(info['preferences_saved'])

    def test_enabled_schema_failure_does_not_reenable_or_disable(self):
        self.installed_skirt()
        self.mark_enabled()
        self.adapter._backend = lambda: (_ for _ in ()).throw(ValueError('exact handler proof failed'))
        info = dependency.status('SKIRT', refresh=True)
        self.assertEqual(info['state'], 'INCOMPATIBLE')
        self.assertIn('exact handler', info['message'])
        with self.assertRaisesRegex(ValueError, 'exact handler'):
            dependency.enable('SKIRT')
        self.assertFalse(self.calls)

    def test_modified_source_refuses_before_official_enable(self):
        module = self.installed_skirt()
        Path(module.__file__).write_bytes(b'edited source')
        with self.assertRaisesRegex(ValueError, 'source differs'):
            dependency.enable('SKIRT')
        self.assertFalse(self.calls)

    def test_multiple_installed_candidates_are_ambiguous(self):
        self.installed_skirt('one')
        self.installed_skirt('two')
        info = dependency.status('SKIRT', refresh=True)
        self.assertEqual(info['state'], 'AMBIGUOUS')
        self.assertFalse(info['enable_available'])

    def test_unknown_registered_native_schema_refuses_without_override(self):
        self.installed_skirt()
        self.native['schema'] = [('Scene', 'wiggle', 'POINTER', 'ForeignWiggleScene')]
        info = dependency.status('SKIRT', refresh=True)
        self.assertEqual(info['state'], 'CONFLICT')
        with self.assertRaisesRegex(ValueError, 'already present'):
            dependency.enable('SKIRT')
        self.assertFalse(self.calls)

    def test_noninstalled_handler_leftover_refuses_activation(self):
        self.installed_skirt()
        self.native['wiggle_named_handlers'] = ['wiggle_pre']
        with self.assertRaisesRegex(ValueError, 'already present'):
            dependency.enable('SKIRT')
        self.assertFalse(self.calls)

    def test_already_ready_is_verified_without_registration_changes(self):
        self.installed_skirt()
        self.mark_enabled()
        self.assertEqual(dependency.enable('SKIRT')['state'], 'READY')
        self.assertFalse(self.calls)

    def test_enable_postcheck_failure_rolls_back_only_new_module(self):
        self.installed_skirt()
        self.adapter._backend = lambda: (_ for _ in ()).throw(ValueError('registered schema mismatch'))
        with self.assertRaisesRegex(ValueError, 'registered schema mismatch'):
            dependency.enable('SKIRT')
        self.assertEqual([call[0] for call in self.calls], ['enable', 'disable'])
        self.assertNotIn('wiggle_2', self.enabled)
        self.assertTrue(dependency.status('SKIRT')['cleanup_exact'])

    def test_partial_registration_check_false_is_not_claimed_clean(self):
        self.installed_skirt()
        def partial(name, **kwargs):
            self.calls.append(('enable', name, kwargs))
            self.native['schema'] = [('Object', 'wiggle_partial', 'BOOLEAN', None)]
            kwargs['handle_error'](RuntimeError('registration interrupted'))
            return None
        self.addons.enable = partial
        with self.assertRaisesRegex(ValueError, 'cleanup is incomplete'):
            dependency.enable('SKIRT')
        info = dependency.status('SKIRT')
        self.assertEqual(info['state'], 'CLEANUP_FAILED')
        self.assertFalse(info['cleanup_exact'])
        self.assertFalse(info['enable_available'])
        self.assertEqual([call[0] for call in self.calls], ['enable', 'disable'])

    def test_source_checked_before_enable_error_report_keeps_actual_exception(self):
        self.installed_skirt()
        self.addons.enable = lambda name, **kwargs: (kwargs['handle_error'](RuntimeError('concrete register error')) or None)
        with self.assertRaisesRegex(ValueError, 'concrete register error'):
            dependency.enable('SKIRT')

    def test_verified_start_updates_cache_without_catalog_scan(self):
        module = self.installed_skirt()
        self.mark_enabled()
        self.addons.modules = lambda **kwargs: (_ for _ in ()).throw(AssertionError('start rescanned'))
        dependency.note_verified('SKIRT', module)
        info = dependency.status('SKIRT')
        self.assertEqual(info['state'], 'READY')
        self.assertTrue(info['enabled'])
        self.assertIsNone(info['installed'])
        self.assertEqual(dependency.status('HAIR')['state'], 'READY')

    def test_unknown_shared_native_field_blocks_both_features(self):
        self.installed_skirt()
        self.mark_enabled()
        self.native['schema'].append(('Scene', 'wiggle_unknown', 'BOOLEAN', 999))
        info = dependency.status('HAIR', refresh=True)
        self.assertEqual(info['state'], 'INCOMPATIBLE')
        self.assertIn('unknown shared fields', info['message'])
        self.assertEqual(dependency.status('SKIRT')['state'], 'INCOMPATIBLE')
        with self.assertRaisesRegex(ValueError, 'unknown shared'):
            dependency.note_verified('HAIR', self.modules[0])
        self.assertFalse(self.calls)

    def test_known_live_schema_tokens_allow_only_own_inactive_orphans(self):
        module = self.installed_skirt()
        self.mark_enabled(module.__name__)
        self.native['schema'] = [(owner, 'wiggle', 'POINTER', 12345 + index)
                                for index, owner in enumerate(('Scene', 'Object', 'PoseBone'))]
        with patch.dict(sys.modules, {module.__name__: module}):
            self.assertEqual(dependency.status('SKIRT', refresh=True)['state'], 'READY')
            self.enabled.clear()
            info = dependency.status('SKIRT', refresh=True)
            self.assertEqual(info['state'], 'DISABLED')
            self.assertTrue(info['verified_flat_residue'])
            self.native['schema'] = [('Scene', 'wiggle', 'POINTER', 67890)]
            self.assertEqual(dependency.status('SKIRT', refresh=True)['state'], 'CONFLICT')

    def test_schema_inspector_never_dereferences_pointer_fixed_type(self):
        class DangerousProperty:
            identifier = 'wiggle'
            type = 'POINTER'
            def as_pointer(self):
                return 12345
            @property
            def fixed_type(self):
                raise AssertionError('native dangling target was dereferenced')
        prop = DangerousProperty()
        owners = {name: type(name, (), {'bl_rna': SimpleNamespace(properties=[prop])})
                  for name in ('Scene', 'Object', 'PoseBone')}
        fake_bpy = SimpleNamespace(types=SimpleNamespace(**owners), app=SimpleNamespace(handlers=SimpleNamespace(
            **{name: [] for name in ('frame_change_pre', 'frame_change_post', 'render_pre',
                                    'render_post', 'render_cancel', 'load_post')})))
        with patch.dict(sys.modules, {'bpy': fake_bpy}):
            state = native_registration_state()
        self.assertEqual(state['schema'], [(name, 'wiggle', 'POINTER', 12345)
                         for name in ('Object', 'PoseBone', 'Scene')])
        self.assertFalse(state['classes'])


if __name__ == '__main__':
    unittest.main()
