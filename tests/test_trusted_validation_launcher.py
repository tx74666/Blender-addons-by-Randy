"""No Blender: verify native launches cannot share the real preference target."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


TOOL = Path(__file__).resolve().parents[1] / 'tools/run_trusted_blender_validation.py'
spec = importlib.util.spec_from_file_location('trusted_validation_launcher_under_test', TOOL)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class ValidationConfigIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cd-config-isolation-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / 'X'
        self.validation = self.project / 'validation'
        self.validation.mkdir(parents=True)
        self.script = self.validation / 'probe.py'
        self.script.write_text('pass\n', encoding='utf-8')
        self.blender = self.root / 'blender.exe'
        self.blender.write_bytes(b'not an executable; Popen is mocked')
        self.original = self.root / 'real-config'
        self.original.mkdir()
        (self.original / 'userpref.blend').write_bytes(b'artist preference bytes')
        (self.original / 'startup.blend').write_bytes(b'artist startup bytes')
        self.record = self.validation / 'proof.json'
        self.isolation = self.validation / 'proof_config'
        self.argv = ['run_trusted_blender_validation.py', '--blender', str(self.blender),
            '--script', str(self.script), '--stdout', str(self.validation/'stdout.log'),
            '--stderr', str(self.validation/'stderr.log'), '--record', str(self.record)]

    def invoke(self, extra=(), popen=None):
        with mock.patch.object(launcher, 'X_PROJECT', self.project), \
             mock.patch.object(launcher, 'SCRIPT_ROOTS', (self.validation,)), \
             mock.patch.dict(os.environ, {'BLENDER_USER_CONFIG': str(self.original)}, clear=True), \
             mock.patch('sys.argv', self.argv + list(extra)), \
             mock.patch.object(launcher.subprocess, 'Popen', side_effect=popen,
                               return_value=types.SimpleNamespace(pid=12345)) as child, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            launcher.main()
            return child

    def test_factory_overrides_inherited_config_and_native_save_stays_isolated(self):
        before = {path.name:path.read_bytes() for path in self.original.iterdir()}
        def native_launch(command, **options):
            self.assertIn('--factory-startup', command)
            target = Path(options['env']['BLENDER_USER_CONFIG'])
            self.assertEqual(target, self.isolation)
            self.assertEqual(list(target.iterdir()), [])
            # Simulate the exact incident: a GUI Save Preferences during a
            # factory test must target only the child's resource directory.
            (target/'userpref.blend').write_bytes(b'factory GUI saved preferences')
            return types.SimpleNamespace(pid=12345)
        self.invoke(popen=native_launch)
        self.assertEqual(before, {path.name:path.read_bytes() for path in self.original.iterdir()})
        receipt=json.loads(self.record.read_bytes())
        self.assertEqual(receipt['config_isolation']['mode'], 'empty_factory')
        self.assertFalse(receipt['config_isolation']['original_config_written'])
        self.assertEqual(receipt['config_isolation']['copied_files'], {})

    def test_user_startup_copies_and_pins_both_inputs_without_writing_original(self):
        before={path.name:path.read_bytes() for path in self.original.iterdir()}
        def native_launch(command, **options):
            self.assertNotIn('--factory-startup', command)
            target=Path(options['env']['BLENDER_USER_CONFIG'])
            self.assertEqual(before, {path.name:path.read_bytes() for path in target.iterdir()})
            (target/'userpref.blend').write_bytes(b'changed by own process only')
            return types.SimpleNamespace(pid=12345)
        self.invoke(['--user-startup'], popen=native_launch)
        receipt=json.loads(self.record.read_bytes())
        isolation=receipt['config_isolation']
        self.assertEqual(isolation['mode'], 'copied_user_startup')
        self.assertEqual(isolation['source_config'], str(self.original))
        for name,data in before.items():
            self.assertEqual(isolation['copied_files'][name]['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(isolation['copied_files'][name]['bytes'], len(data))
        self.assertEqual(before, {path.name:path.read_bytes() for path in self.original.iterdir()})

    def test_existing_isolation_directory_is_never_reused(self):
        self.isolation.mkdir()
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertFalse(self.record.exists())

    def test_record_outside_workspace_cannot_create_config_or_launch(self):
        outside=self.root/'outside'
        index=self.argv.index('--record')+1
        self.argv[index]=str(outside/'proof.json')
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertFalse(outside.exists())
        self.assertFalse(self.isolation.exists())
        self.assertFalse((self.validation/'stdout.log').exists())

    def test_missing_saved_preferences_refuses_user_startup(self):
        empty=self.root/'empty-config'
        empty.mkdir()
        with self.assertRaises(SystemExit):
            self.invoke(['--user-startup','--user-config-source',str(empty)])
        self.assertFalse(self.isolation.exists())
        self.assertFalse(self.record.exists())

    def test_isolation_symlink_cannot_redirect_writes_outside_workspace(self):
        outside=self.root/'outside'
        outside.mkdir()
        try:
            self.isolation.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest('This host cannot create a directory symlink: '+str(error))
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.record.exists())


if __name__ == '__main__':
    unittest.main()
