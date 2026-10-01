"""Pure contract/lifecycle checks. Does not import Blender or start a process."""

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
PACKAGE = '_animation_link_contract_fixture'
package = ModuleType(PACKAGE)
package.__path__ = [str(SOURCE)]
sys.modules[PACKAGE] = package


def load(name):
    spec = importlib.util.spec_from_file_location(PACKAGE + '.' + name, SOURCE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    setattr(package, name, module)
    return module


link = load('animation_link')


class Item(dict):
    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='animation-link-contract-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.packet = self.root / 'Walk.cdanim.json'
        self.packet.write_bytes(b'Frozen packet bytes')
        self.path = self.root / link.MANIFEST_NAME
        self.manifest = {'schema': link.SCHEMA, 'linkId': str(uuid.uuid4()),
            'targetGuid': 'a' * 32, 'clipGuid': 'b' * 32, 'clipLocalId': 1657602633327794031,
            'sourcePackage': str(self.packet), 'sourcePackageSha256': link._sha256(self.packet),
            'unityOnly': {'nested': [1, 'retain me']}, 'fbxFile': 'previous revision'}
        self.write_manifest(self.manifest)
        self.model = self.root / 'Character.fbx'
        self.model.write_bytes(b'Frozen model')
        self.slot = SimpleNamespace(handle=17, target_id_type='OBJECT')
        self.action = Item(name='Edited Walk', library=None, frame_range=(1.25, 30.75), slots=[self.slot])
        self.action[link.PACKAGE_KEY] = str(self.packet)
        self.action[link.PACKAGE_HASH_KEY] = self.manifest['sourcePackageSha256']
        self.action['unity_loop_time'], self.action['unity_sample_rate'] = True, 60
        self.rig = Item(name='Authoring Rig', type='ARMATURE', library=None,
                        animation_data=SimpleNamespace(action=self.action, action_slot=self.slot))
        self.bpy = SimpleNamespace(data=SimpleNamespace(objects=[self.rig], filepath=str(self.root / 'Editing.blend')))
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {'bpy': self.bpy}).start()
        unity_export = ModuleType(PACKAGE + '.unity_export')
        unity_export._ACTIVE_JOB = None
        unity_export._filename = lambda name, rig: name
        patch.dict(sys.modules, {PACKAGE + '.unity_export': unity_export}).start()
        package.unity_export = unity_export
        self.exporter = load('animation_export')
        self.context = SimpleNamespace(scene=SimpleNamespace(render=SimpleNamespace(fps=24, fps_base=1.001)))

    def write_manifest(self, value):
        self.path.write_text(json.dumps(value), encoding='utf8')

    def bind(self):
        link.bind_action(self.rig, self.action, self.path, 'CoshaRig', self.model)

    def revision(self, destination=None):
        directory = self.root / 'blender_revisions' / uuid.UUID(self.manifest['linkId']).hex
        fbx = destination or directory / (uuid.uuid4().hex + '.fbx')
        fbx.parent.mkdir(parents=True, exist_ok=True)
        fbx.write_bytes(b'Completed unique FBX')
        metadata = fbx.with_suffix('.animation.json')
        digest = link._sha256(fbx)
        metadata.write_text(json.dumps({'fbx_sha256': digest, 'source_package': str(self.packet),
            'source_package_sha256': self.manifest['sourcePackageSha256'], 'action': self.action.name}), encoding='utf8')
        return {'filepath': str(fbx), 'metadata': str(metadata), 'fbx_sha256': digest}

    def finalize(self, result):
        return link.publish_linked_revision(self.path, self.manifest, result,
            blend_file=self.bpy.data.filepath, source_action=self.action.name,
            model_file=str(self.model), model_sha256=link._sha256(self.model))

    def test_load_retains_unknown_fields_and_exact_long(self):
        record = link.load_link(self.path)
        self.assertEqual(record['clipLocalId'], 1657602633327794031)
        self.assertIs(type(record['clipLocalId']), int)
        self.assertEqual(record['unityOnly'], self.manifest['unityOnly'])
        self.assertEqual(record['_manifest_path'], str(self.path))
        for invalid in (float(self.manifest['clipLocalId']), True, 2**63, 0, '123'):
            self.write_manifest({**self.manifest, 'clipLocalId': invalid})
            with self.assertRaises(link.AnimationLinkError):
                link.load_link(self.path)

    def test_binding_is_persistent_draw_is_io_free_and_duplicate_is_rejected(self):
        self.bind()
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Draw read a file')), \
                patch.object(Path, 'resolve', side_effect=AssertionError('Draw resolved a path')):
            record = link.get_link(self.rig)
        self.assertEqual(record['link_id'], self.manifest['linkId'])
        self.assertEqual(record['action_slot'], 17)
        self.assertIs(self.rig[link.ACTION_KEY], self.action)
        duplicate = Item(name='Duplicated Rig', type='ARMATURE', library=None, animation_data=self.rig.animation_data)
        duplicate.update(self.rig)
        self.bpy.data.objects.append(duplicate)
        with self.assertRaisesRegex(link.AnimationLinkError, 'duplicated'):
            link.begin_linked_export(self.context, self.rig)
        self.assertFalse(Path(str(self.path) + '.blender.lock').exists())

    def test_wrong_packet_binding_is_nonmutating(self):
        self.action[link.PACKAGE_HASH_KEY] = 'f' * 64
        with self.assertRaisesRegex(link.AnimationLinkError, 'not imported'):
            self.bind()
        self.assertEqual(dict(self.rig), {})

    def test_revision_commit_preserves_unknown_current_fields(self):
        result = self.revision()
        fresh = {**self.manifest, 'unityOnly': {'changedWhileBaking': True}, 'unityNewField': [3, 4]}
        self.write_manifest(fresh)
        published = self.finalize(result)
        record = json.loads(self.path.read_text(encoding='utf8'))
        self.assertEqual(record['unityOnly'], fresh['unityOnly'])
        self.assertEqual(record['unityNewField'], [3, 4])
        self.assertEqual(record['clipLocalId'], self.manifest['clipLocalId'])
        self.assertEqual(record['fbxFile'], result['filepath'])
        self.assertEqual(record['metadataSha256'], link._sha256(result['metadata']))
        self.assertEqual(published['link_manifest'], str(self.path))
        self.assertEqual(record['revision'], 1)
        self.assertEqual(published['revision'], 1)
        self.assertNotIn('_manifest_path', record)
        self.assertFalse(Path(str(self.path) + '.blender.lock').exists())

    def test_revision_increments_only_with_successful_manifest_publication(self):
        first = self.finalize(self.revision())
        self.assertEqual(first['revision'], 1)
        second = self.finalize(self.revision())
        self.assertEqual(second['revision'], 2)
        previous = self.path.read_bytes()
        with patch.object(link.os, 'replace', side_effect=OSError('Injected publication failure')):
            with self.assertRaisesRegex(OSError, 'publication failure'):
                self.finalize(self.revision())
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(json.loads(previous)['revision'], 2)
        self.assertFalse(Path(str(self.path) + '.blender.lock').exists())
        self.assertFalse(list(self.root.glob('*.animation-link.tmp')))

    def test_invalid_and_overflow_revision_leave_manifest_unchanged(self):
        result = self.revision()
        for revision in (True, False, -1, 1.0, '1', None, 2**31 - 1, 2**31):
            with self.subTest(revision=revision):
                self.write_manifest({**self.manifest, 'revision': revision})
                previous = self.path.read_bytes()
                with self.assertRaisesRegex(link.AnimationLinkError, 'Link revision'):
                    self.finalize(result)
                self.assertEqual(self.path.read_bytes(), previous)
                self.assertFalse(Path(str(self.path) + '.blender.lock').exists())

    def test_failed_publication_keeps_previous_link_and_revision_files(self):
        result = self.revision()
        original = self.path.read_bytes()
        self.packet.write_bytes(b'Changed packet')
        with self.assertRaisesRegex(link.AnimationLinkError, 'packet changed'):
            self.finalize(result)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(Path(result['filepath']).is_file())
        self.assertFalse(Path(str(self.path) + '.blender.lock').exists())
        self.packet.write_bytes(b'Frozen packet bytes')
        self.write_manifest({**self.manifest, 'clipGuid': 'c' * 32})
        changed = self.path.read_bytes()
        with self.assertRaisesRegex(link.AnimationLinkError, 'identity changed'):
            self.finalize(result)
        self.assertEqual(self.path.read_bytes(), changed)

    def test_tampered_pair_and_foreign_output_are_rejected(self):
        result = self.revision()
        original = self.path.read_bytes()
        Path(result['metadata']).write_text('{}', encoding='utf8')
        with self.assertRaises(link.AnimationLinkError):
            self.finalize(result)
        outside = self.revision(self.root / 'unrelated.fbx')
        with self.assertRaisesRegex(link.AnimationLinkError, 'revision directory'):
            self.finalize(outside)
        self.assertEqual(self.path.read_bytes(), original)

    def test_optional_model_identity_cannot_change_while_exporting(self):
        result = self.revision()
        added = {**self.manifest, 'modelFile': str(self.model), 'modelSha256': link._sha256(self.model)}
        for expected, current in ((self.manifest, added), (added, self.manifest),
                                  (added, {**added, 'modelSha256': 'd' * 64}),
                                  (added, {**added, 'modelFile': str(self.root / 'Other.fbx')})):
            with self.subTest(expected=expected.get('modelFile'), current=current.get('modelFile')):
                self.write_manifest(current)
                before = self.path.read_bytes()
                with self.assertRaisesRegex(link.AnimationLinkError, 'identity changed'):
                    link.publish_linked_revision(self.path, expected, result,
                        blend_file=self.bpy.data.filepath, source_action=self.action.name)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse(Path(str(self.path) + '.blender.lock').exists())
        self.write_manifest(added)
        self.bind()
        self.assertEqual(link.get_link(self.rig)['identity']['modelSha256'], added['modelSha256'])

    def test_bound_model_changes_and_wrong_model_binding_are_rejected(self):
        self.write_manifest({**self.manifest, 'modelFile': str(self.model), 'modelSha256': 'd' * 64})
        with self.assertRaisesRegex(link.AnimationLinkError, 'model hash differs'):
            self.bind()
        self.write_manifest(self.manifest)
        self.bind()
        self.model.write_bytes(b'Modified bound model')
        with patch.object(self.exporter, 'begin_export') as begin:
            with self.assertRaisesRegex(link.AnimationLinkError, 'bound model changed'):
                link.begin_linked_export(self.context, self.rig)
            begin.assert_not_called()
        self.assertFalse(Path(str(self.path) + '.blender.lock').exists())

    def test_exclusive_lock_does_not_delete_foreign_lock(self):
        lock = link._LinkLock(self.path)
        with self.assertRaises(link.AnimationLinkError):
            link._LinkLock(self.path)
        lock.path.write_text('Replaced by another owner', encoding='ascii')
        lock.release()
        self.assertTrue(lock.path.exists())
        lock.path.unlink()

    def test_begin_uses_bound_action_current_range_and_releases_after_finalize(self):
        self.bind()
        captured = {}
        job = {'temporary': SimpleNamespace(cleanup=lambda: None)}
        def begin(context, rig, action, destination, **options):
            captured.update(rig=rig, action=action, destination=destination, options=options)
            return job
        with patch.object(self.exporter, 'begin_export', side_effect=begin):
            self.assertIs(link.begin_linked_export(self.context, self.rig), job)
        self.assertIs(captured['action'], self.action)
        self.assertEqual(captured['options'], {'frame_start': 1.25, 'frame_end': 30.75,
            'loop': True, 'sample_rate': 60., 'export_rig_name': 'CoshaRig'})
        lockfile = Path(str(self.path) + '.blender.lock')
        self.assertTrue(lockfile.exists())
        result = self.revision(captured['destination'])
        job['_publish_callback'](job, result)
        self.exporter._dispose(job)
        self.assertFalse(lockfile.exists())
        self.assertEqual(json.loads(self.path.read_text())['fbxFile'], result['filepath'])

    def test_slot_change_and_worker_start_failure_leave_no_lock(self):
        self.bind()
        self.rig.animation_data.action_slot = SimpleNamespace(handle=18, target_id_type='OBJECT')
        with self.assertRaisesRegex(link.AnimationLinkError, 'slot differs'):
            link.begin_linked_export(self.context, self.rig)
        self.rig.animation_data.action_slot = self.slot
        with patch.object(self.exporter, 'begin_export', side_effect=RuntimeError('Worker could not start')):
            with self.assertRaisesRegex(RuntimeError, 'could not start'):
                link.begin_linked_export(self.context, self.rig)
        self.assertFalse(Path(str(self.path) + '.blender.lock').exists())

    def test_dispose_callback_on_worker_failure_cancel_timeout_and_publish_failure(self):
        for mode in ('worker_failure', 'cancel', 'timeout', 'publish_failure'):
            with self.subTest(mode=mode):
                stage = self.root / mode
                stage.mkdir()
                (stage / 'result.json').write_text(json.dumps({'ok': mode == 'publish_failure'}))
                (stage / 'worker.log').write_text('Expected worker failure')
                pending = mode in ('cancel', 'timeout')
                process = SimpleNamespace(poll=lambda: None if pending else 0, returncode=0,
                    terminate=lambda: None, wait=lambda **kwargs: None)
                released = []
                job = {'process': process, 'stage': stage, 'root': stage, 'started': 0,
                    'temporary': SimpleNamespace(cleanup=lambda: None),
                    '_dispose_callback': lambda: released.append(True),
                    '_publish_callback': lambda *_: (_ for _ in ()).throw(RuntimeError('Manifest failure'))}
                self.exporter._ACTIVE_JOB = job
                if mode == 'cancel':
                    self.exporter.cancel_export(job)
                else:
                    with patch.object(self.exporter, '_publish', return_value={}), patch.object(self.exporter.time, 'monotonic', return_value=601):
                        with self.assertRaises((self.exporter.AnimationExportError, RuntimeError)):
                            self.exporter.poll_export(job)
                self.assertEqual(released, [True])
                self.assertIsNone(self.exporter.active_job())


if __name__ == '__main__':
    unittest.main()
