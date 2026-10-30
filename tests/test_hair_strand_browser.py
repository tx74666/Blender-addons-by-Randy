"""Browser metadata transactions preserve physical settings and native authorship."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profiles = load('browser_real_profiles', ROOT / 'hair_motion_profiles.py')
PACKAGE = '_hair_browser_test_package'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
hair = types.ModuleType(PACKAGE + '.hair_bones_rig')
hair.RECORD_KEY, hair.RIG_KEY = 'binding', 'rig'
lifecycle = types.ModuleType(PACKAGE + '.hair_motion_lifecycle')
registry = types.ModuleType(PACKAGE + '.hair_strand_registry')
registry.REGISTRY_KEY = 'registry'
resegment = types.ModuleType(PACKAGE + '.hair_resegment')
with patch.dict(sys.modules, {PACKAGE: package,
        PACKAGE + '.hair_bones_rig': hair, PACKAGE + '.hair_motion_lifecycle': lifecycle,
        PACKAGE + '.hair_motion_profiles': profiles,
        PACKAGE + '.hair_resegment': resegment,
        PACKAGE + '.hair_strand_registry': registry}):
    browser = load(PACKAGE + '.hair_strand_browser', ROOT / 'hair_strand_browser.py')


class Source(dict):
    def __init__(self, *args, name='Hair', session_uid=100, **kwargs):
        super().__init__(*args, **kwargs)
        self.name, self.session_uid = name, session_uid
        self.library = self.override_library = None
        self.is_editable = True
        self.writes = []
        self.fail_key = None
        self.fail_after = False

    def __setitem__(self, key, value):
        self.writes.append(('set', key))
        if self.fail_key == key:
            self.fail_key = None
            if self.fail_after:
                super().__setitem__(key, value)
            raise RuntimeError('injected property write failure')
        super().__setitem__(key, value)

    def __delitem__(self, key):
        self.writes.append(('delete', key))
        super().__delitem__(key)


def data_fixture():
    result = {'version': 1, 'source_uid': '1' * 32, 'topology': '2' * 64,
              'strands': []}
    for order, (signature, count, side, partner) in enumerate((
            ('left', 3, 'L', 'right'), ('right', 3, 'R', 'left'),
            ('center', 2, 'C', 'center'), ('independent', 5, 'U', None))):
        result['strands'].append({'signature': signature, 'strand_id': signature,
            'order': order, 'side': side, 'pair_id': 'pair' if partner else None,
            'mirror_id': partner, 'bones': [f'发束 {signature} {index}' for index in range(count)]})
    return result


class HairStrandBrowserTests(unittest.TestCase):
    def setUp(self):
        self.data = data_fixture()
        self.record = {'source_id': self.data['source_uid'], 'chains': [], 'mirror_layout': None}
        self.rig = types.SimpleNamespace(name='Rig', session_uid=101)
        self.root = types.SimpleNamespace(name='Root Hair', session_uid=102)
        self.source = Source({'binding': json.dumps(self.record), 'rig': self.rig,
                              'artist': {'weights': [.2, .8], 'locks': [False, True],
                                         'actions': ['walk'], 'rest': [1, 2, 3]}})
        hair._read_records = Mock(side_effect=lambda source: json.loads(source['binding']))
        lifecycle.source_uid_for_bind = Mock(return_value=self.data['source_uid'])
        registry.read = Mock(side_effect=lambda source, validate=True:
                             json.loads(source['registry']) if 'registry' in source else None)
        registry._live = Mock(side_effect=lambda source, check_layers=False:
                              (copy.deepcopy(self.data), json.loads(source['binding']), ()))
        registry._mirror_provenance = Mock()
        registry._validate_links = Mock(side_effect=lambda value: value)
        registry._validate_mesh_pairs = Mock(side_effect=lambda value, geometry: value)
        resegment.root_source = Mock(return_value=self.root)
        resegment.guard_switch = Mock()

    def initialized(self, *, sync=True):
        self.source['registry'] = json.dumps(self.data, indent=3)
        profile = browser._default_profile(self.data)
        profile['group_defaults']['UNASSIGNED']['depth'][0]['mass'] = 3.25
        for name in ('left', 'right'):
            profile['strands'][name]['sync_mirror'] = sync
            profile['strands'][name]['overrides'] = {'gravity': 6.125, 'stretch': .2}
        profile['strands']['independent']['group'] = 'BACK'
        profile['strands']['independent']['overrides'] = {'damping': .33}
        self.source[profiles.PROFILE_KEY] = json.dumps(profile, indent=4)
        self.source.writes.clear()
        return profile

    def assert_no_writes(self, before):
        self.assertEqual(dict(self.source), before)
        self.assertEqual(self.source.writes, [])

    def test_invalid_source_uses_existing_native_valueerror_before_metadata_access(self):
        resegment.root_source.side_effect = ValueError('Choose an existing bound Hair mesh.')
        for source in (None, object()):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'bound Hair mesh'):
                browser.strict_snapshot(source)
        hair._read_records.assert_not_called()
        lifecycle.source_uid_for_bind.assert_not_called()
        self.assertEqual(self.source.writes, [])
    def test_missing_metadata_read_is_strict_detached_and_never_initializes(self):
        before = dict(self.source)
        snapshot = browser.strict_snapshot(self.source)
        self.assertEqual(tuple(row['signature'] for row in snapshot['rows']),
                         ('left', 'right', 'center', 'independent'))
        self.assertEqual([row['count'] for row in snapshot['rows']], [3, 3, 2, 5])
        self.assertEqual(snapshot['rows'][0]['bone_label'], '发束 left 0')
        self.assertEqual(snapshot['rows'][0]['mirror_signature'], 'right')
        self.assertEqual(snapshot['fingerprint']['registry'], {'missing': True})
        self.assertEqual(snapshot['fingerprint']['profile'], {'missing': True})
        self.assertEqual(snapshot['fingerprint']['source']['session_uid'], '100')
        self.assertEqual(json.loads(json.dumps(snapshot['fingerprint'])), snapshot['fingerprint'])
        lifecycle.source_uid_for_bind.assert_called_once_with(self.source, self.record)
        registry._live.assert_called_once_with(self.source, check_layers=True)
        registry._validate_mesh_pairs.assert_called_once()
        registry.read.assert_called_once_with(self.source, validate=True)
        registry._mirror_provenance.assert_not_called()
        self.assert_no_writes(before)
        snapshot['rows'][0]['group'] = 'BACK'
        self.assertEqual(browser.list_rows(self.source)[0]['group'], 'UNASSIGNED')
        self.assert_no_writes(before)

    def test_only_saved_native_mirror_layout_asks_for_provenance(self):
        self.record['mirror_layout'] = 'NATIVE'
        self.source['binding'] = json.dumps(self.record)
        self.source.writes.clear()
        browser.strict_snapshot(self.source)
        registry._mirror_provenance.assert_called_once()
        self.assertEqual(self.source.writes, [])

    def test_existing_registry_is_read_with_strict_validation(self):
        self.initialized()
        before = dict(self.source)
        browser.strict_snapshot(self.source)
        registry.read.assert_called_once_with(self.source, validate=True)
        registry._live.assert_not_called()
        self.assert_no_writes(before)

    def test_native_lifecycle_or_registry_failure_refuses_without_writes(self):
        self.initialized()
        before = dict(self.source)
        for dependency in (lifecycle.source_uid_for_bind, registry.read):
            with self.subTest(dependency=dependency):
                original = dependency.side_effect
                dependency.side_effect = ValueError('native proof failed')
                with self.assertRaisesRegex(ValueError, 'native proof'):
                    browser.strict_snapshot(self.source)
                self.assert_no_writes(before)
                dependency.side_effect = original

    def test_malformed_present_metadata_never_becomes_a_missing_default(self):
        for key, raw in (('registry', None), ('registry', '{broken'),
                         (profiles.PROFILE_KEY, None), (profiles.PROFILE_KEY, '{broken')):
            with self.subTest(key=key, raw=raw):
                self.setUp()
                self.source[key] = raw
                self.source.writes.clear()
                before = dict(self.source)
                with self.assertRaises((ValueError, TypeError)):
                    browser.strict_snapshot(self.source)
                self.assert_no_writes(before)

    def test_missing_defaults_same_group_request_is_noop_without_initializing(self):
        before = dict(self.source)
        result = browser.assign_category(None, self.source, ['left'], 'UNASSIGNED')
        self.assertFalse(result['changed'])
        self.assertEqual(result['changed_signatures'], ())
        resegment.guard_switch.assert_not_called()
        self.assert_no_writes(before)

    def test_changed_category_preserves_all_effective_physics_and_unchecked_records(self):
        before_profile = self.initialized()
        before_source = dict(self.source)
        before_effective = profiles.effective_all(self.source, registry=self.data)
        expected = browser.strict_snapshot(self.source)['fingerprint']
        result = browser.assign_category('context', self.source, ['right', 'left'], 'FRONT',
                                         expected_fingerprint=expected)
        self.assertTrue(result['changed'])
        self.assertEqual(result['changed_signatures'], ('left', 'right'))
        after = profiles.read(self.source, registry=self.data)
        after_effective = profiles.effective_all(self.source, registry=self.data)
        self.assertEqual(after['group_defaults'], before_profile['group_defaults'])
        for name in before_effective:
            self.assertEqual({k: v for k, v in before_effective[name].items() if k != 'group'},
                             {k: v for k, v in after_effective[name].items() if k != 'group'})
        for name in ('center', 'independent'):
            self.assertEqual(after['strands'][name], before_profile['strands'][name])
        self.assertEqual(self.source['registry'], before_source['registry'])
        self.assertEqual(self.source['artist'], before_source['artist'])
        self.assertEqual(self.source.writes, [('set', profiles.PROFILE_KEY)])
        resegment.guard_switch.assert_called_once_with('context', self.source)
        self.assertEqual(after['strands']['left']['overrides']['depth'][0]['mass'], 3.25)

    def test_unsynchronized_single_side_keeps_partner_exact(self):
        before = self.initialized(sync=False)
        browser.assign_category(None, self.source, ('left',), 'SIDE')
        after = profiles.read(self.source, registry=self.data)
        self.assertEqual(after['strands']['right'], before['strands']['right'])
        self.assertFalse(after['strands']['left']['sync_mirror'])
        self.assertEqual(after['strands']['left']['group'], 'SIDE')

    def test_center_self_pair_needs_only_itself(self):
        self.initialized()
        browser.assign_category(None, self.source, ('center',), 'BACK')
        after = profiles.read(self.source, registry=self.data)
        self.assertEqual(after['strands']['center']['group'], 'BACK')

    def test_synchronized_single_side_refuses_before_guard_and_any_write(self):
        self.initialized()
        before = dict(self.source)
        with self.assertRaisesRegex(ValueError, 'both synchronized'):
            browser.assign_category(None, self.source, ['left'], 'FRONT')
        resegment.guard_switch.assert_not_called()
        self.assert_no_writes(before)

    def test_invalid_inputs_and_stale_fingerprint_refuse_before_writes(self):
        self.initialized()
        expected = browser.strict_snapshot(self.source)['fingerprint']
        cases = [('left', 'FRONT', None), ([], 'FRONT', None),
                 (['left', 'left'], 'FRONT', None), (['missing'], 'FRONT', None),
                 (['left', 'right'], 'front', None), (['left', 'right'], None, None),
                 (['left', 'right'], 'FRONT', {}), (['left', 'right'], 'FRONT', {'bad': object()})]
        for signatures, group, fingerprint in cases:
            before = dict(self.source)
            with self.subTest(signatures=signatures, group=group), self.assertRaises(ValueError):
                browser.assign_category(None, self.source, signatures, group,
                                         expected_fingerprint=fingerprint)
            self.assert_no_writes(before)
        self.source.session_uid += 1
        with self.assertRaisesRegex(ValueError, 'scope changed'):
            browser.assign_category(None, self.source, ['left', 'right'], 'FRONT',
                                     expected_fingerprint=expected)
        self.assertEqual(self.source.writes, [])

    def test_raw_format_change_also_invalidates_fingerprint(self):
        self.initialized()
        expected = browser.strict_snapshot(self.source)['fingerprint']
        self.source['registry'] = json.dumps(self.data, separators=(',', ':'))
        self.source.writes.clear()
        before = dict(self.source)
        with self.assertRaisesRegex(ValueError, 'scope changed'):
            browser.assign_category(None, self.source, ['left', 'right'], 'FRONT',
                                     expected_fingerprint=expected)
        self.assert_no_writes(before)

    def test_family_guard_and_noneditable_source_refuse_without_mutation(self):
        self.initialized()
        before = dict(self.source)
        resegment.guard_switch.side_effect = ValueError('active family recovery')
        with self.assertRaisesRegex(ValueError, 'active family recovery'):
            browser.assign_category(None, self.source, ['left', 'right'], 'FRONT')
        self.assert_no_writes(before)
        resegment.guard_switch.side_effect = None
        self.source.is_editable = False
        with self.assertRaisesRegex(ValueError, 'local and editable'):
            browser.assign_category(None, self.source, ['left', 'right'], 'FRONT')
        self.assert_no_writes(before)

    def test_guard_scope_change_is_detected_immediately_before_write(self):
        self.initialized()
        resegment.guard_switch.side_effect = lambda context, source: setattr(self.rig, 'session_uid', 999)
        before = dict(self.source)
        with self.assertRaisesRegex(ValueError, 'scope changed before saving'):
            browser.assign_category(None, self.source, ['left', 'right'], 'FRONT')
        self.assert_no_writes(before)

    def test_first_explicit_change_persists_registry_profile_only(self):
        before = dict(self.source)
        result = browser.assign_category(None, self.source, ['left', 'right'], 'SIDE')
        self.assertTrue(result['changed'])
        self.assertEqual(self.source.writes, [('set', 'registry'), ('set', profiles.PROFILE_KEY)])
        self.assertEqual(self.source['artist'], before['artist'])
        self.assertEqual(self.source['binding'], before['binding'])
        after = profiles.read(self.source, registry=self.data)
        for name in ('left', 'right'):
            effective = profiles._effective_validated(after, name)
            self.assertEqual(effective['gravity'], profiles.DEFAULTS['UNASSIGNED']['gravity'])
        self.assertEqual(result['fingerprint']['registry']['sha256'],
                         browser.strict_snapshot(self.source)['fingerprint']['registry']['sha256'])

    def test_write_fault_restores_exact_raw_or_absence_for_both_keys(self):
        for initialized in (False, True):
            for key in ('registry', profiles.PROFILE_KEY):
                if initialized and key == 'registry':
                    continue
                for after_write in (False, True):
                    with self.subTest(initialized=initialized, key=key, after_write=after_write):
                        self.setUp()
                        if initialized:
                            self.initialized()
                        before = dict(self.source)
                        self.source.fail_key, self.source.fail_after = key, after_write
                        with self.assertRaisesRegex(RuntimeError, 'injected'):
                            browser.assign_category(None, self.source, ['left', 'right'], 'FRONT')
                        self.assertEqual(dict(self.source), before)

    def test_postwrite_strict_validation_fault_rolls_back_all_metadata(self):
        for initialized in (False, True):
            with self.subTest(initialized=initialized):
                self.setUp()
                if initialized:
                    self.initialized()
                before = dict(self.source)
                calls = 0
                def proof(source, record):
                    nonlocal calls
                    calls += 1
                    if calls > 1:
                        raise ValueError('postwrite native proof failure')
                    return self.data['source_uid']
                lifecycle.source_uid_for_bind.side_effect = proof
                with self.assertRaisesRegex(ValueError, 'postwrite'):
                    browser.assign_category(None, self.source, ['left', 'right'], 'FRONT')
                self.assertEqual(dict(self.source), before)

    def test_candidate_source_identity_mismatch_refuses_without_writes(self):
        lifecycle.source_uid_for_bind.return_value = '3' * 32
        before = dict(self.source)
        with self.assertRaisesRegex(ValueError, 'ownership differs'):
            browser.strict_snapshot(self.source)
        self.assert_no_writes(before)


if __name__ == '__main__':
    unittest.main()

