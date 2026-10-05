"""Pure settings transactions, stable identity, group and depth semantics."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/hair_motion_profiles.py'
spec = importlib.util.spec_from_file_location('hair_motion_profiles', PATH)
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


def fixture():
    source = {'artist': {'weights': [0.2, 0.8], 'bones': 128, 'segments': 4}}
    registry = {'version': 1, 'source_uid': '1' * 32, 'topology': '2' * 64, 'strands': [
        {'strand_id': 'left', 'mirror_id': 'right', 'pair_id': 'pair'},
        {'strand_id': 'right', 'mirror_id': 'left', 'pair_id': 'pair'},
        {'strand_id': 'center', 'mirror_id': None, 'pair_id': None},
        {'strand_id': 'third', 'mirror_id': None, 'pair_id': None}]}
    profiles.initialize(source, registry=registry)
    return source, registry


class Profiles(unittest.TestCase):
    def test_effective_all_checks_whole_profile_once_and_returns_detached_depth(self):
        for count in (2, 32):
            with self.subTest(strands=count):
                source = {}
                registry = {'version': 1, 'source_uid': '1' * 32, 'topology': '2' * 64,
                            'strands': [{'strand_id': f'strand-{index}', 'mirror_id': None, 'pair_id': None}
                                        for index in range(count)]}
                record = profiles.initialize(source, registry=registry)
                for index, item in enumerate(record['strands'].values()):
                    item['overrides']['depth'] = [
                        {'position': knot / 23, 'recovery': .7, 'damping': .7,
                         'mass': 1 + index / 32, 'gravity': 4.0} for knot in range(24)]
                profiles._write(source, record, registry)
                before = copy.deepcopy(source)
                with patch.object(profiles, '_validate', wraps=profiles._validate) as validation:
                    values = profiles.effective_all(source, registry=registry)
                self.assertEqual(validation.call_count, 1)
                self.assertEqual(len(values), count)
                self.assertEqual(values[f'strand-{count - 1}']['depth'][0]['mass'], 1 + (count - 1) / 32)
                values[f'strand-{count - 1}']['depth'][0]['mass'] = 10.0
                self.assertEqual(values['strand-0']['depth'][0]['mass'], 1.0)
                self.assertEqual(source, before)
                self.assertEqual(profiles.read(source, registry=registry), record)

                # The single full validation still examines corruption on the
                # final strand before exposing any requested preview settings.
                broken = copy.deepcopy(record)
                broken['strands'][f'strand-{count - 1}']['overrides']['depth'][-1]['position'] = .5
                with patch.object(profiles, '_validate', wraps=profiles._validate) as validation:
                    with self.assertRaises(profiles.HairMotionProfileError):
                        profiles.effective_all(source, registry=registry, record=broken)
                self.assertEqual(validation.call_count, 1)
                self.assertEqual(source, before)

    def test_initialize_and_read_are_repeatable_and_preserve_author_data(self):
        source, registry = fixture()
        before = copy.deepcopy(source)
        record = profiles.initialize(source, registry=registry)
        self.assertEqual(source, before)
        record['group_defaults']['FRONT']['recovery'] = 0.1
        self.assertEqual(source, before)
        self.assertGreater(profiles.DEFAULTS['FRONT']['recovery'], profiles.DEFAULTS['BACK']['recovery'])

    def test_pair_settings_sync_only_and_center_remains_separate(self):
        source, registry = fixture()
        original = copy.deepcopy(source['artist'])
        profiles.update(source, 'left', group='FRONT', overrides={'recovery': 0.8}, registry=registry)
        values = profiles.effective_all(source, registry=registry)
        self.assertEqual(values['left'], values['right'])
        self.assertEqual(values['center']['group'], 'UNASSIGNED')
        self.assertEqual(source['artist'], original)

    def test_disable_pair_sync_is_reciprocal_and_keeps_values_independent(self):
        source, registry = fixture()
        profiles.update(source, 'left', sync_mirror=False, registry=registry)
        profiles.update(source, 'left', overrides={'gravity': 2}, registry=registry)
        profiles.update(source, 'right', overrides={'gravity': 9}, registry=registry)
        values = profiles.effective_all(source, registry=registry)
        self.assertFalse(values['right']['sync_mirror'])
        self.assertEqual(values['left']['gravity'], 2)
        self.assertEqual(values['right']['gravity'], 9)

    def test_enable_sync_copies_source_settings(self):
        source, registry = fixture()
        profiles.update(source, 'left', sync_mirror=False, registry=registry)
        profiles.update(source, 'left', group='BACK', overrides={'gravity': 8}, registry=registry)
        values = profiles.effective(source, 'left', registry=registry)
        profiles.update(source, 'left', sync_mirror=True, group=values['group'],
                        overrides={key: values[key] for key in profiles.RANGES.keys() | {'depth'}}, registry=registry)
        values = profiles.effective_all(source, registry=registry)
        self.assertEqual(values['left'], values['right'])

    def test_group_apply_and_restore(self):
        source, registry = fixture()
        profiles.update(source, 'left', group='FRONT', registry=registry)
        profiles.update(source, 'third', group='FRONT', registry=registry)
        profiles.update(source, 'left', overrides={'damping': 0.2}, registry=registry)
        profiles.apply_to_group(source, 'left', registry=registry)
        profiles.update(source, 'third', overrides={'damping': 0.7}, registry=registry)
        profiles.restore_group_defaults(source, 'third', registry=registry)
        self.assertEqual(profiles.effective(source, 'third', registry=registry)['damping'], 0.2)
        self.assertEqual(profiles.read(source, registry=registry)['strands']['left']['overrides'], {})

    def test_invalid_settings_fail_before_write(self):
        source, registry = fixture()
        before = copy.deepcopy(source)
        for settings in ({'gravity': float('nan')}, {'damping': 1.1}, {'recovery': True},
                         {'bone_count': 2}, {'depth': []}, {'stretch': -1}):
            with self.assertRaises(profiles.HairMotionProfileError):
                profiles.update(source, 'left', overrides=settings, registry=registry)
            self.assertEqual(source, before)

    def test_partial_mirror_proof_cannot_force_match(self):
        source, registry = fixture()
        registry['strands'][1]['mirror_id'] = None
        before = copy.deepcopy(source)
        with self.assertRaises(profiles.HairMotionProfileError):
            profiles.update(source, 'left', overrides={'gravity': 8}, registry=registry)
        self.assertEqual(source, before)

    def test_depth_sample_independent_of_segment_count(self):
        source, registry = fixture()
        settings = profiles.effective(source, 'left', registry=registry)
        self.assertAlmostEqual(profiles.depth_sample(settings, 0.5)['recovery'], 0.5775)
        source['artist']['segments'] = 8
        self.assertEqual(profiles.effective(source, 'left', registry=registry), settings)
        knots = settings['depth']
        knots.insert(1, {'position': 0.3, 'recovery': 0.1, 'damping': 0.2, 'mass': 2.0, 'gravity': 3.0})
        profiles.update(source, 'left', overrides={'depth': knots}, registry=registry)
        self.assertEqual(profiles.depth_sample(profiles.effective(source, 'right', registry=registry), 0.3)['mass'], 2)

    def test_base_edits_scale_existing_curve(self):
        source, registry = fixture()
        profiles.update(source, 'left', overrides={'recovery': 1.0}, registry=registry)
        settings = profiles.effective(source, 'left', registry=registry)
        self.assertEqual(settings['depth'][0]['recovery'], 1)
        self.assertAlmostEqual(settings['depth'][-1]['recovery'], 0.65)

    def test_rename_and_explicit_refresh_keep_stable_settings(self):
        source, registry = fixture()
        profiles.update(source, 'left', overrides={'gravity': 8}, registry=registry)
        registry['strands'].reverse()
        registry['strands'][0]['bones'] = ['Artist Rename']
        self.assertEqual(profiles.effective(source, 'left', registry=registry)['gravity'], 8)
        registry['topology'] = '3' * 64
        with self.assertRaises(profiles.HairMotionProfileError):
            profiles.read(source, registry=registry)
        profiles.reconcile(source, registry=registry)
        self.assertEqual(profiles.effective(source, 'left', registry=registry)['gravity'], 8)

    def test_reconcile_refuses_lost_identity(self):
        source, registry = fixture()
        before = copy.deepcopy(source)
        registry['strands'].pop()
        with self.assertRaises(profiles.HairMotionProfileError):
            profiles.reconcile(source, registry=registry)
        self.assertEqual(source, before)

    def test_corruption_and_foreign_source_are_read_only(self):
        source, registry = fixture()
        source[profiles.PROFILE_KEY] = '{broken'
        before = copy.deepcopy(source)
        with self.assertRaises(profiles.HairMotionProfileError):
            profiles.read(source, registry=registry)

        self.assertEqual(source, before)
        source, registry = fixture()
        registry['source_uid'] = 'foreign'
        with self.assertRaises(profiles.HairMotionProfileError):
            profiles.read(source, registry=registry)

    def test_initial_group_suggestions_are_editable_and_keep_pairs_together(self):
        source, registry = fixture()
        for item, length, y in zip(registry['strands'], (0.1, 0.11, 1.0, 0.2), (-.1, -.09, .1, .05)):
            item['rest'] = [{'head': [0.1, y, 1], 'tail': [.1, y, 1 - length]}]
            item['anchor'] = {'head': [0, 0, .8], 'tail': [0, 0, 1]}
        groups = profiles.suggested_groups(registry)
        self.assertEqual(groups, {'left': 'FRONT', 'right': 'FRONT', 'center': 'BACK', 'third': 'SIDE'})
        profiles.assign_suggested_groups(source, registry=registry)
        profiles.update(source, 'left', group='SIDE', registry=registry)
        profiles.assign_suggested_groups(source, registry=registry)
        self.assertEqual(profiles.effective(source, 'right', registry=registry)['group'], 'SIDE')

    def test_malformed_strand_cannot_be_reconciled_into_a_pair(self):
        source, registry = fixture()
        record = json.loads(source[profiles.PROFILE_KEY])
        record['strands']['left'] = None
        source[profiles.PROFILE_KEY] = json.dumps(record)
        before = copy.deepcopy(source)
        with self.assertRaises(profiles.HairMotionProfileError):
            profiles.reconcile(source, registry=registry)
        self.assertEqual(source, before)


if __name__ == '__main__':
    unittest.main()
