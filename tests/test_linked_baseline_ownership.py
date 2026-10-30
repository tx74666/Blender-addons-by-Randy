"""Source-cache ownership handoff only; no Blender/native loader acceptance."""

import ast
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_worklist.py'


class NativeID(SimpleNamespace):
    __hash__ = object.__hash__


class LinkedBaselineOwnership(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd-source-ownership-')
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.saved = SimpleNamespace(workspace_id=str(uuid.uuid4()), model_sha256='f' * 64)
        self.item_id = uuid.uuid4().hex
        self.path = self.cache / uuid.UUID(self.saved.workspace_id).hex / self.saved.model_sha256 / (self.item_id + '.blend')
        self.action = NativeID(name='Artist draft')
        self.linked = SimpleNamespace(name='Linked source', library=SimpleNamespace(filepath=str(self.path)))
        self.selected = SimpleNamespace(actions=[])
        self.write_error = self.load_error = self.digest_error = None
        self.calls = []
        fixture = self

        class Loader:
            def __enter__(self):
                fixture.calls.append('enter')
                return SimpleNamespace(actions=[fixture.action.name]), fixture.selected

            def __exit__(self, *_args):
                fixture.calls.append('exit')
                fixture.selected.actions = [fixture.linked]
                if fixture.load_error is not None:
                    raise fixture.load_error

        def write(path, actions, **kwargs):
            self.calls.append(('write', path, actions, kwargs))
            Path(path).write_bytes(b'private tiny source cache')
            if self.write_error is not None:
                raise self.write_error

        def load(path, **kwargs):
            self.calls.append(('load', path, kwargs))
            return Loader()

        def digest(path):
            if self.digest_error is not None:
                raise self.digest_error
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()

        bpy = SimpleNamespace(utils=SimpleNamespace(user_resource=lambda *_a, **_k: str(self.cache)),
                              data=SimpleNamespace(libraries=SimpleNamespace(write=write, load=load)))
        namespace = {'Path': Path, 'uuid': uuid, 'bpy': bpy, 'links': SimpleNamespace(_sha256=digest)}
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_linked_baseline')
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        self.run_baseline = namespace['_linked_baseline']

    def run_with_receipt(self, receipt):
        return self.run_baseline(self.saved, self.action, self.item_id, 'Walk', _ownership=receipt)

    def test_success_hands_off_actual_loader_ids_and_its_unique_file(self):
        receipt = {}
        linked, path, digest = self.run_with_receipt(receipt)
        self.assertIs(linked, self.linked)
        self.assertEqual(path, str(self.path))
        self.assertEqual(digest, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(self.action.name, 'Source · Walk')
        self.assertEqual(receipt['path'], path)
        self.assertIs(receipt['input_action'], self.action)
        self.assertIs(receipt['load_result'], self.selected)
        self.assertIs(receipt['action'], self.linked)
        self.assertIs(receipt['library'], self.linked.library)
        self.assertNotIn('failed', receipt)

    def test_partial_native_load_failure_keeps_file_and_actual_result_for_owner_cleanup(self):
        receipt = {}
        self.load_error = RuntimeError('partial loader exit')
        with self.assertRaisesRegex(RuntimeError, 'partial loader exit'):
            self.run_with_receipt(receipt)
        self.assertTrue(receipt['failed'])
        self.assertIs(receipt['load_result'].actions[0], self.linked)
        self.assertEqual(receipt['path'], str(self.path))
        self.assertTrue(self.path.is_file())
        self.assertNotIn('action', receipt)
        self.assertNotIn('library', receipt)

    def test_partial_write_failure_keeps_file_with_explicit_failed_ownership(self):
        receipt = {}
        self.write_error = OSError('partial write')
        with self.assertRaisesRegex(OSError, 'partial write'):
            self.run_with_receipt(receipt)
        self.assertTrue(self.path.is_file())
        self.assertTrue(receipt['failed'])
        self.assertEqual(receipt['path'], str(self.path))
        self.assertIs(receipt['input_action'], self.action)
        self.assertNotIn('load_result', receipt)
        self.assertEqual(len(self.calls), 1)

    def test_hash_failure_keeps_file_for_explicit_owner(self):
        receipt = {}
        self.digest_error = OSError('hash read')
        with self.assertRaisesRegex(OSError, 'hash read'):
            self.run_with_receipt(receipt)
        self.assertTrue(self.path.is_file())
        self.assertTrue(receipt['failed'])
        self.assertNotIn('load_result', receipt)

    def test_legacy_default_failure_retains_existing_own_cache_cleanup_behavior(self):
        self.load_error = RuntimeError('partial loader exit')
        with self.assertRaisesRegex(RuntimeError, 'partial loader exit'):
            self.run_baseline(self.saved, self.action, self.item_id, 'Walk')
        self.assertFalse(self.path.exists())
        self.assertIs(self.selected.actions[0], self.linked)

    def test_existing_cache_is_never_claimed_or_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'pre-existing baseline')
        receipt = {}
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_with_receipt(receipt)
        self.assertEqual(receipt, {})
        self.assertEqual(self.path.read_bytes(), b'pre-existing baseline')
        self.assertEqual(self.calls, [])

    def test_invalid_or_reused_receipt_refuses_before_action_or_cache_write(self):
        for receipt in ({'foreign': 'receipt'}, [], 'receipt'):
            with self.subTest(receipt=receipt):
                with self.assertRaisesRegex(ValueError, 'empty dictionary'):
                    self.run_with_receipt(receipt)
                self.assertEqual(self.action.name, 'Artist draft')
                self.assertFalse(self.path.exists())
                self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
