"""Source identity, persistence and meaningful Cloth goal semantics."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/skirt_motion_profiles.py'
spec = importlib.util.spec_from_file_location('skirt_motion_profiles', PATH)
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


def setup():
    return {'owner': 'Dress-owner', 'chain_count': 8, 'segment_count': 4}


class DressProfiles(unittest.TestCase):
    def test_profile_roundtrip_is_detached_and_preserves_artist_data(self):
        source = {'artist_note': 'Keep', 'weights': [0.2, 0.8]}
        profile = profiles.fresh(setup())
        profiles.write(source, profile, setup())
        readback = profiles.read(source, setup())
        self.assertEqual(profile, readback)
        readback['settings']['bend'] = 2.0
        self.assertEqual(profiles.read(source, setup()), profile)
        self.assertEqual(source['weights'], [0.2, 0.8])

    def test_changed_owner_or_structure_rejects_without_rewriting_record(self):
        source = {}
        profiles.write(source, profiles.fresh(setup()), setup())
        before = copy.deepcopy(source)
        for key, value in (('owner', 'Foreign'), ('chain_count', 4), ('segment_count', 6)):
            with self.subTest(key=key), self.assertRaises(profiles.DressMotionError):
                profiles.read(source, {**setup(), key: value})
            self.assertEqual(source, before)

    def test_invalid_numbers_unknown_fields_and_modes_are_rejected(self):
        original = profiles.fresh(setup())
        for changes in ({'mass': float('nan')}, {'mass': True}, {'quality': 1.5},
                        {'gravity': -1}, {'self_collision': 1}, {'magic_inertia': .5}):
            with self.subTest(changes=changes), self.assertRaises(profiles.DressMotionError):
                profiles.edited(original, setup(), changes)
        for capability, mode in (('MANUAL', 'AUTOMATIC'), ('PHYSICS', 'MANUAL'), ('OTHER', 'MANUAL')):
            with self.subTest(capability=capability), self.assertRaises(profiles.DressMotionError):
                profiles.fresh(setup(), capability=capability, mode=mode)
        self.assertEqual(original, profiles.fresh(setup()))

    def test_legacy_pin_pattern_matches_and_recovery_does_not_pin_hem(self):
        weights = profiles.pin_weights(profiles.DEFAULTS, 13, 32)
        self.assertEqual(weights[:32], [1.0] * 32)
        self.assertEqual(weights[32:64], [.35] * 32)
        self.assertEqual(weights[64:], [0.0] * (11 * 32))
        values = {**profiles.DEFAULTS, 'recovery': 1.0, 'waist_depth': .17}
        weights = profiles.pin_weights(values, 13, 32)
        self.assertEqual(weights[:96], [1.0] * 96)
        self.assertEqual(weights[96:128], [.35] * 32)
        self.assertTrue(all(0 < value < .08 for value in weights[128:]))
        self.assertEqual(weights[-32:], [.005] * 32)
        for row in range(13):
            self.assertEqual(len(set(weights[row * 32:(row + 1) * 32])), 1)

    def test_settings_change_keeps_mode_and_source_identity(self):
        original = profiles.fresh(setup())
        updated = profiles.edited(original, setup(), {'bend': 2, 'damping': 8}, mode='MANUAL')
        self.assertEqual(updated['identity'], original['identity'])
        self.assertEqual(updated['settings']['mass'], original['settings']['mass'])
        self.assertEqual(updated['settings']['bend'], 2.0)
        self.assertEqual(updated['mode'], 'MANUAL')
        self.assertEqual(original['mode'], 'AUTOMATIC')


if __name__ == '__main__':
    unittest.main()
