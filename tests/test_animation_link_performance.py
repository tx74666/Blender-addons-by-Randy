"""Bounded packet-reuse/publication checks; no Blender or child process runs."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'
BLOCK_SIZE = 1024 * 1024


class Item(dict):
    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)


class FakeAction(Item):
    pass


class IsolatedModules(unittest.TestCase):
    def setUp(self):
        # Avoid importing the add-on entry point or contaminating other fixtures.
        self.package_name = '_animation_performance_' + uuid.uuid4().hex
        self.package = ModuleType(self.package_name)
        self.package.__path__ = [str(SOURCE)]
        bpy = ModuleType('bpy')
        bpy.types = SimpleNamespace(Action=FakeAction)
        mathutils = ModuleType('mathutils')
        mathutils.Matrix, mathutils.Vector = object, object
        retarget = ModuleType(self.package_name + '.animation_retarget')
        retarget._new_channelbag = retarget._write_curve = None
        unity_export = ModuleType(self.package_name + '.unity_export')
        unity_export._filename = lambda name, _rig: name
        self.modules = patch.dict(sys.modules, {
            self.package_name: self.package, 'bpy': bpy, 'mathutils': mathutils,
            retarget.__name__: retarget, unity_export.__name__: unity_export,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def load(self, name):
        qualified = self.package_name + '.' + name
        spec = importlib.util.spec_from_file_location(qualified, SOURCE / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
        setattr(self.package, name, module)
        return module


class PublicationTests(IsolatedModules):
    def setUp(self):
        super().setUp()
        self.exporter = self.load('animation_export')
        temporary = tempfile.TemporaryDirectory(prefix='animation-publication-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.stage, self.output = self.root / 'worker-stage', self.root / 'published'
        self.stage.mkdir()
        self.destination = self.output / 'Walk.fbx'
        self.metadata = self.destination.with_suffix('.animation.json')
        self.source = self.stage / self.destination.name
        self.job = {
            'destination': self.destination, 'metadata': self.metadata, 'stage': self.stage,
            'source_blend': str(self.root / 'Editing.blend'), 'action': 'Edited Walk',
            'source_package': str(self.root / 'Walk.cdanim.json'),
            'source_package_sha256': 'a' * 64,
        }
        self.worker_result = {'ok': True, 'duration': 0.9666666667, 'sample_rate': 60.0}

    def test_multiblock_publication_hashes_one_bounded_source_stream(self):
        payload = bytes(range(256)) * 8192 + b'partial final block'
        self.assertGreater(len(payload), 2 * BLOCK_SIZE)
        self.source.write_bytes(payload)
        open_original = Path.open
        source_opens, read_sizes = [], []
        owner = self

        class BoundedReader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def read(self, size=-1):
                owner.assertGreater(size, 0, 'The source must not be read unbounded.')
                owner.assertLessEqual(size, BLOCK_SIZE)
                read_sizes.append(size)
                return self.stream.read(size)

        def audited_open(path, mode='r', *args, **kwargs):
            stream = open_original(path, mode, *args, **kwargs)
            if path == self.source and mode == 'rb':
                source_opens.append(path)
                return BoundedReader(stream)
            return stream

        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Whole-file allocation')), \
                patch.object(Path, 'open', audited_open):
            result = self.exporter._publish(self.job, self.worker_result)

        self.assertEqual(source_opens, [self.source])
        self.assertGreaterEqual(len(read_sizes), 4)  # Two blocks, a tail and EOF.
        expected_hash = hashlib.sha256(payload).hexdigest()
        self.assertEqual(self.destination.read_bytes(), payload)
        self.assertEqual(result['fbx_sha256'], expected_hash)
        report = json.loads(self.metadata.read_text(encoding='utf-8'))
        self.assertEqual(report['fbx_sha256'], expected_hash)
        self.assertEqual(report['source_package_sha256'], self.job['source_package_sha256'])
        self.assertEqual(report['source_package'], self.job['source_package'])
        self.assertEqual(report['origin'], 'unity-edit')
        self.assertEqual(report['duration'], self.worker_result['duration'])
        self.assertEqual(result['metadata'], str(self.metadata))
        self.assertEqual(result['filepath'], str(self.destination))
        self.assertEqual(set(self.output.iterdir()), {self.destination, self.metadata})
        self.assertEqual(self.source.read_bytes(), payload)

    def test_existing_output_is_preserved_and_partial_publication_removed(self):
        self.source.write_bytes(b'new worker FBX')
        self.output.mkdir()
        for existing in (self.metadata, self.destination):
            with self.subTest(existing=existing.name):
                sentinel = b'previous output must survive'
                existing.write_bytes(sentinel)
                with self.assertRaises(FileExistsError):
                    self.exporter._publish(self.job, self.worker_result)
                self.assertEqual(existing.read_bytes(), sentinel)
                self.assertEqual(set(self.output.iterdir()), {existing})
                self.assertEqual(self.source.read_bytes(), b'new worker FBX')
                existing.unlink()

    def test_second_publication_failure_cleans_new_metadata_and_staging(self):
        self.source.write_bytes(b'complete worker FBX')
        original_link = self.exporter.os.link
        calls = []

        def fail_fbx_publication(src, dst):
            calls.append(Path(dst))
            if Path(dst) == self.destination:
                self.assertTrue(self.metadata.is_file())
                raise OSError('Injected second publication failure')
            return original_link(src, dst)

        with patch.object(self.exporter.os, 'link', side_effect=fail_fbx_publication):
            with self.assertRaisesRegex(OSError, 'second publication failure'):
                self.exporter._publish(self.job, self.worker_result)
        self.assertEqual(calls, [self.metadata, self.destination])
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), b'complete worker FBX')


class ImportBoundaryTests(IsolatedModules):
    def setUp(self):
        super().setUp()
        self.unity = self.load('unity_animation')
        self.target = Item(name='CoshaRig')
        self.context = SimpleNamespace(object=SimpleNamespace(mode='OBJECT'))

    def set_active_preview(self):
        action = FakeAction(name='Existing preview')
        action[self.unity.VERSION_KEY] = 1
        action[self.unity.TARGET_KEY] = self.target
        self.target[self.unity.ACTIVE_KEY] = action

    def test_link_reuses_packet_when_current_animation_module_is_available(self):
        source = self.load('animation_link_source')
        packet = {'_path': 'Walk.cdanim.json', '_sha256': 'b' * 64}
        expected = object()
        with patch.object(self.unity, '_import_package_action', return_value=expected) as convert, \
                patch.object(self.unity, 'import_test_action') as public:
            self.assertIs(source._import_action(self.context, self.target, packet, 4), expected)
        convert.assert_called_once_with(self.context, self.target, packet, start_frame=4)
        public.assert_not_called()

    def test_file_update_remains_compatible_with_cached_previous_animation_module(self):
        source = self.load('animation_link_source')
        source.unity_animation = SimpleNamespace(import_test_action=lambda *args, **kwargs: None)
        packet = {'_path': 'Walk.cdanim.json', '_sha256': 'b' * 64}
        expected = object()
        with patch.object(source.unity_animation, 'import_test_action', return_value=expected) as public:
            self.assertIs(source._import_action(self.context, self.target, packet, 4), expected)
        public.assert_called_once_with(self.context, self.target, 'Walk.cdanim.json', start_frame=4)

    def test_public_wrapper_loads_once_and_passes_same_validated_dictionary(self):
        packet = {'_path': 'Walk.cdanim.json', '_sha256': 'b' * 64, 'frames': []}
        expected_result = object()
        with patch.object(self.unity, 'load_package', return_value=packet) as load, \
                patch.object(self.unity, '_import_package_action', return_value=expected_result) as import_packet:
            result = self.unity.import_test_action(self.context, self.target, 'Walk.cdanim.json', start_frame=17.5)
        self.assertIs(result, expected_result)
        load.assert_called_once_with('Walk.cdanim.json')
        import_packet.assert_called_once_with(self.context, self.target, packet, start_frame=17.5)
        self.assertIs(import_packet.call_args.args[2], packet)

    def test_public_guards_reject_before_file_load_or_delegation(self):
        for mode in ('preview', 'edit'):
            with self.subTest(mode=mode):
                self.target.clear()
                self.context.object.mode = 'OBJECT'
                if mode == 'preview':
                    self.set_active_preview()
                else:
                    self.context.object.mode = 'EDIT'
                with patch.object(self.unity, 'load_package') as load, \
                        patch.object(self.unity, '_import_package_action') as import_packet:
                    with self.assertRaisesRegex(self.unity.UnityAnimationError, 'Restore|Leave Edit Mode'):
                        self.unity.import_test_action(self.context, self.target, 'unread.cdanim.json')
                load.assert_not_called()
                import_packet.assert_not_called()

    def test_private_guards_reject_before_conversion_without_blender(self):
        for mode in ('preview', 'edit'):
            with self.subTest(mode=mode):
                self.target.clear()
                self.context.object.mode = 'OBJECT'
                if mode == 'preview':
                    self.set_active_preview()
                else:
                    self.context.object.mode = 'EDIT'
                with patch.object(self.unity, 'load_package') as load, \
                        patch.object(self.unity, '_mapping') as mapping:
                    with self.assertRaisesRegex(self.unity.UnityAnimationError, 'Restore|Leave Edit Mode'):
                        self.unity._import_package_action(self.context, self.target, {}, start_frame=3)
                load.assert_not_called()
                mapping.assert_not_called()


if __name__ == '__main__':
    unittest.main()
