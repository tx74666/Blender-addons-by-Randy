"""Recovery packet IO composed with the existing native-ownership test fixture."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_animation_worklist_target as fixture


class DependencyRecoveryTests(fixture.TargetTests):
    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(prefix='cd-recovery-dependencies-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        spec = importlib.util.spec_from_file_location('_recovery_dependency_core',
            fixture.SOURCE / 'animation_worklist_dependencies.py')
        self.core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.core)
        path = self.folder / 'unity-packet.json'
        path.write_text(json.dumps({'fixture': 'immutable packet'}), encoding='utf-8')
        self.packet = {'_path': str(path), '_sha256': self.core._hash(path)}
        self.worklist._prepared.return_value = ({}, {}, {}, self.packet, 'Rig', Path('historical.fbx'))
        self.dependencies.pin_recovery_packet.side_effect = lambda packet: self.core.pin_recovery_packet(
            packet, self.folder / 'recovery-cache')
        self.dependencies.packet_path = self.core.packet_path
        self.ua.load_package = lambda path: dict(json.loads(Path(path).read_text()),
            _path=str(path), _sha256=self.core._hash(path))
        sys.modules['bpy'].path = SimpleNamespace(abspath=str)
        self.rig.animation_data = None

    def adopt_for_restore(self):
        self.adopt()
        self.original.active.return_value = False
        return self.target._load(self.rig)

    def test_dependency_new_session_restores_after_original_packet_moves(self):
        record = self.adopt_for_restore()
        self.assertNotEqual(record['packet_path'], self.packet['_path'])
        Path(self.packet['_path']).unlink()
        self.target.restore_main_rig(self.context)
        self.assertNotIn(self.target.SESSION, self.rig)
        self.target._apply_capture.assert_called_once()

    def test_dependency_new_session_uses_pinned_bytes_when_exchange_changes(self):
        self.adopt_for_restore()
        Path(self.packet['_path']).write_text('replacement exchange packet')
        self.target.restore_main_rig(self.context)
        self.assertNotIn(self.target.SESSION, self.rig)

    def test_dependency_tampered_recovery_cache_keeps_checkpoint_and_pose(self):
        record = self.adopt_for_restore()
        before = self.target.capture_session_metadata(self.rig)
        Path(record['packet_path']).write_text(json.dumps({'fixture': 'tampered'}))
        with self.assertRaisesRegex(ValueError, 'packet changed'):
            self.target.restore_main_rig(self.context)
        self.assertEqual(self.target.capture_session_metadata(self.rig), before)
        self.target._apply_capture.assert_not_called()
        self.assertIs(self.rig.data, self.data)

    def test_dependency_legacy_session_retains_strict_original_path_hash(self):
        record = self.adopt_for_restore()
        record['packet_path'] = self.packet['_path']
        self.target._store(self.rig, record)
        before = self.target.capture_session_metadata(self.rig)
        Path(self.packet['_path']).write_text(json.dumps({'fixture': 'new revision'}))
        with self.assertRaisesRegex(ValueError, 'packet changed'):
            self.target.restore_main_rig(self.context)
        self.assertEqual(self.target.capture_session_metadata(self.rig), before)
        self.target._apply_capture.assert_not_called()

    def test_dependency_pin_failure_precedes_original_leave_and_scene_writes(self):
        before = dict(self.rig)
        Path(self.packet['_path']).write_text('changed between preparation and pin')
        with self.assertRaisesRegex(ValueError, 'changed before copying'):
            self.adopt()
        self.original.leave.assert_not_called()
        self.target._apply_capture.assert_not_called()
        self.assertEqual(dict(self.rig), before)
        self.assertIsNone(self.saved.rig)

    def test_dependency_failed_adoption_preserves_cached_bytes_and_original_metadata(self):
        self.target._validate_capture.side_effect = ValueError('native proof failed')
        before = dict(self.rig)
        with self.assertRaisesRegex(ValueError, 'native proof failed'):
            self.adopt()
        self.assertEqual(dict(self.rig), before)
        cached = list((self.folder / 'recovery-cache').glob('*.cdanim.json'))
        self.assertEqual(len(cached), 1)
        self.assertEqual(self.core._hash(cached[0]), self.packet['_sha256'])
        self.target._apply_capture.assert_called_once()


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(DependencyRecoveryTests(name) for name in
        loader.getTestCaseNames(DependencyRecoveryTests) if name.startswith('test_dependency_'))


if __name__ == '__main__':
    unittest.main()
