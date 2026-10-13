"""OriginalInputs admission/file-pin sequencing, not native Blender validation.

Runs the production current/file helper and real _pin/_sha on temporary files.
native.current is an explicitly simulated admission boundary: its injected Body,
owner and track failures test propagation, not actual Blender RNA validation.
"""
import ast
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
          / 'unity_animation_original_inputs.py')
FILE_KEYS = ('prepared_report', 'clock_certificate', 'snapshot_receipt', 'packet',
             'legacy_packet', 'manifest', 'source_cache', 'prepared_model')


class CurrentAdmissionTests(unittest.TestCase):
    def setUp(self):
        names = {'_require', '_sha', '_pin', '_validate_pair_files', 'current'}
        nodes = [node for node in ast.parse(SOURCE.read_text(encoding='utf-8')).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in nodes}, names,
                         'Candidate OriginalInputs file-pin helper is not implemented.')
        self.ns = dict(Path=Path, hashlib=hashlib)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)
        temp = TemporaryDirectory(prefix='original-inputs-current-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.pair, self.contents = {}, {}
        for key in FILE_KEYS:
            data = ('owned ' + key).encode('utf-8')
            path = self.root / (key + '.json')
            path.write_bytes(data)
            self.contents[key] = data
            self.pair[key] = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
        self.rig = object()
        self.session = {'original_inputs': self.pair}
        self.events = []
        self.native_failure = None
        # A stale display cache must never replace failed authoritative admission.
        self.native = NS(current=self.native_current, _CACHE={1: self.session})
        self.ns.update(native=self.native, validate_session=self.duplicate_validation)
        self.actual_pin = self.ns['_pin']
        self.ns['_pin'] = self.pin

    def native_current(self, rig):
        self.assertIs(rig, self.rig)
        self.events.append(('native', rig))
        if self.native_failure is not None:
            raise self.native_failure
        return self.session

    def duplicate_validation(self, *_args, **_kwargs):
        raise AssertionError('current repeated the native admission instead of checking file pins.')

    def pin(self, path, digest):
        self.events.append(('pin', path, digest))
        return self.actual_pin(path, digest)

    def current(self):
        return self.ns['current'](self.rig)

    def pin_events(self, keys=FILE_KEYS):
        return [('pin', self.pair[key]['path'], self.pair[key]['sha256']) for key in keys]

    def test_native_admission_once_then_exact_eight_live_pins(self):
        result = self.current()
        self.assertIs(result, self.session)
        self.assertEqual(self.events, [('native', self.rig)] + self.pin_events())

    def test_every_call_repeats_native_admission_and_all_file_pins(self):
        self.assertIs(self.current(), self.session)
        self.assertIs(self.current(), self.session)
        self.assertEqual(self.events, ([('native', self.rig)] + self.pin_events()) * 2)

    def test_each_changed_file_rejects_after_prior_success_without_cache_fallback(self):
        self.current()
        for index, key in enumerate(FILE_KEYS):
            with self.subTest(file=key):
                path = Path(self.pair[key]['path'])
                path.write_bytes(self.contents[key] + b' changed')
                self.events.clear()
                try:
                    with self.assertRaisesRegex(ValueError, 'authenticated Original input file changed'):
                        self.current()
                    self.assertEqual(self.events, [('native', self.rig)] + self.pin_events(FILE_KEYS[:index + 1]))
                finally:
                    path.write_bytes(self.contents[key])

    def test_each_missing_file_rejects_without_cached_success(self):
        self.current()
        for key in FILE_KEYS:
            with self.subTest(file=key):
                path = Path(self.pair[key]['path'])
                path.unlink()
                try:
                    with self.assertRaisesRegex(ValueError, 'authenticated Original input file changed'):
                        self.current()
                finally:
                    path.write_bytes(self.contents[key])

    def test_simulated_native_body_owner_or_track_failure_is_authoritative(self):
        for reason in ('Body motion changed', 'native owner changed', 'owned tracks changed'):
            with self.subTest(reason=reason):
                self.events.clear()
                self.native_failure = RuntimeError(reason)
                with self.assertRaises(RuntimeError) as caught:
                    self.current()
                self.assertIs(caught.exception, self.native_failure)
                self.assertEqual(self.events, [('native', self.rig)])

    def test_ordinary_owner_rejected_before_file_checks(self):
        self.session = {'ordinary_rigging_owner': True}
        with self.assertRaisesRegex(ValueError, 'Another ordinary Rigging owner is active'):
            self.current()
        self.assertEqual(self.events, [('native', self.rig)])

    def test_absent_native_session_returns_none_without_file_checks(self):
        self.session = None
        self.assertIsNone(self.current())
        self.assertEqual(self.events, [('native', self.rig)])

    def test_helper_propagates_exact_file_error_and_stops(self):
        failure = OSError('injected unreadable Original input file')
        bad_path = self.pair['legacy_packet']['path']

        def fail_pin(path, digest):
            if path == bad_path:
                self.events.append(('pin', path, digest))
                raise failure
            return self.pin(path, digest)

        self.ns['_pin'] = fail_pin
        with self.assertRaises(OSError) as caught:
            self.current()
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.events, [('native', self.rig)] + self.pin_events(FILE_KEYS[:5]))

    def test_invalid_digest_is_not_accepted_from_previous_success(self):
        self.current()
        self.pair['prepared_model']['sha256'] = '0' * 63
        with self.assertRaisesRegex(ValueError, 'authenticated Original input file changed'):
            self.current()

    def test_missing_file_record_cannot_return_session(self):
        self.pair.pop('source_cache')
        with self.assertRaises(KeyError) as caught:
            self.current()
        self.assertEqual(caught.exception.args, ('source_cache',))


if __name__ == '__main__':
    unittest.main()
