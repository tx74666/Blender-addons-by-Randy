"""Pure export ownership/isolation checks; no Blender or worker process."""
from contextlib import contextmanager
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


class ID(dict):
    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)


def capture_fixture(contract):
    identity = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]
    bindings = []
    for side in ('Right', 'Left'):
        root = side + 'Arm'
        mid, tip = root + '/Forearm', root + '/Forearm/Hand'
        target_parent = 'Rig1' if side == 'Right' else 'RightArm/Forearm/Hand'
        bindings.append(dict(rigPath='Rig1', rigWeight=0.,
            constraintPath='Rig1/' + side + 'Constraint', rootPath=root, midPath=mid, tipPath=tip,
            targetPath=target_parent + '/' + side + 'Target', targetParentPath=target_parent,
            hintPath='Rig1/' + side + 'Hint', hintParentPath='Rig1', constraintWeight=1.,
            targetPositionWeight=1., targetRotationWeight=1., hintWeight=1.,
            maintainTargetPositionOffset=False, maintainTargetRotationOffset=False))
    return dict(schema='cdesigner.two-bone-rigging/1', targetGuid='a' * 32,
        targetDependencyHash='b' * 32, sourceMode='prefab-default', sourceClipPath='',
        sourceClipGuid='', sourceClipLocalId=0, sourceClipSha256='',
        bindingsSha256=contract.bindings_digest(bindings), newActionId='2' * 32,
        duration=1., sampleRate=2., loopTime=False, sourceTimeMode='normalized-base-phase',
        previewEnabled=True, bindings=bindings, frames=[dict(time=time, controls=[
            dict(targetPosition=[1., 2., 3.], targetRotation=[0., 0., 0., 1.], hintPosition=[4., 5., 6.],
                targetParentRoot=identity[:], hintParentRoot=identity[:]) for _ in range(2)])
            for time in (0., .5, 1.)])


class RiggingExportTests(unittest.TestCase):
    def setUp(self):
        self.package_name = '_rigging_export_' + uuid.uuid4().hex
        package = ModuleType(self.package_name)
        package.__path__ = [str(SOURCE)]
        unity_export = ModuleType(self.package_name + '.unity_export')
        unity_export._filename = lambda name, _rig: name
        unity_animation = ModuleType(self.package_name + '.unity_animation')
        unity_animation.native_action_frame_rate = lambda action: action.get('character_designer_unity_native_timebase')
        self.native = ModuleType(self.package_name + '.unity_animation_rigging')
        self.boundary_active = False

        @contextmanager
        def boundary(*_args, **_kwargs):
            self.boundary_active = True
            try:
                yield
            finally:
                self.boundary_active = False

        self.native.base_only = boundary
        self.modules = patch.dict(sys.modules, {self.package_name: package, 'bpy': SimpleNamespace(),
            unity_export.__name__: unity_export, unity_animation.__name__: unity_animation,
            self.native.__name__: self.native})
        self.modules.start()
        self.addCleanup(self.modules.stop)

        def load(name):
            spec = importlib.util.spec_from_file_location(self.package_name + '.' + name, SOURCE / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            setattr(package, name, module)
            return module

        self.contract = load('animation_rigging_contract')
        self.export = load('animation_export')
        self.link = load('animation_link')
        self.base = ID(name='Base edit', slots=[SimpleNamespace(handle=7, target_id_type='OBJECT')])
        self.base['character_designer_unity_animation_package_sha256'] = 'd' * 64
        self.base['character_designer_unity_native_timebase'] = 2.
        self.inputs = ID(name='Target inputs')
        self.rig = ID(animation_data=None)
        self.rig['character_designer_rigging_session_v1'] = 'owned test tag'
        self.session = dict(capture=capture_fixture(self.contract), base_action=self.base, base_slot=7,
            input_action=self.inputs, input_slot=11, new_action_id='2' * 32, work_item_id='1' * 32,
            source_read_only=False)
        self.native.current = lambda _rig: self.session
        samples = [dict(time=frame['time'], controls=[{key: control[key] for key in
            ('targetPosition', 'targetRotation', 'hintPosition')} for control in frame['controls']])
            for frame in self.session['capture']['frames']]
        self.dto = self.contract.build_export(self.session['capture'], samples,
            source_packet_sha256='d' * 64, work_item_id='1' * 32, new_action_id='2' * 32,
            source_action=self.base.name, source_action_slot=7,
            target_input_action=self.inputs.name, target_input_action_slot=11)

    def begin(self, dto=None):
        return self.export.begin_export(None, self.rig, self.base, 'unused.fbx',
            frame_start=1., frame_end=3., rigging_export=dto)

    def test_identity_tamper_rejects_before_snapshot_and_worker(self):
        for field, value in (('sourcePacketSha256', 'e' * 64), ('workItemId', '3' * 32),
                             ('sourceActionSlot', 9), ('targetInputActionSlot', 12)):
            with self.subTest(field=field), patch.object(self.export, '_begin_export') as launch:
                changed = json.loads(json.dumps(self.dto))
                changed[field] = value
                with self.assertRaises(ValueError):
                    self.begin(changed)
                launch.assert_not_called()
                self.assertFalse(self.boundary_active)

    def test_rigging_inputs_cannot_be_silently_omitted(self):
        with patch.object(self.export, '_begin_export') as launch:
            with self.assertRaisesRegex(ValueError, 'Worklist Sync'):
                self.begin()
            launch.assert_not_called()

    def test_clock_mismatch_rejects_before_base_snapshot(self):
        for overrides in (dict(frame_start=2.), dict(frame_end=4.), dict(sample_rate=3.), dict(loop=True),
                          dict(frame_start=True), dict(sample_rate=True)):
            with self.subTest(overrides=overrides), patch.object(self.export, '_begin_export') as launch:
                options = dict(frame_start=1., frame_end=3., rigging_export=self.dto)
                options.update(overrides)
                with self.assertRaisesRegex(ValueError, 'time range'):
                    self.export.begin_export(None, self.rig, self.base, 'unused.fbx', **options)
                launch.assert_not_called()
                self.assertFalse(self.boundary_active)

    def test_snapshot_occurs_inside_base_boundary_and_payload_is_owned(self):
        def launch(*_args, **kwargs):
            self.assertTrue(self.boundary_active)
            return {'rigging_export': kwargs['rigging_export']}
        with patch.object(self.export, '_begin_export', side_effect=launch):
            job = self.begin(self.dto)
        self.assertFalse(self.boundary_active)
        self.dto['frames'][0]['controls'][0]['targetPosition'][0] = 999.
        self.assertEqual(job['rigging_export']['frames'][0]['controls'][0]['targetPosition'][0], 1.)

    def test_start_failure_restores_boundary(self):
        with patch.object(self.export, '_begin_export', side_effect=RuntimeError('mock launch failure')):
            with self.assertRaisesRegex(RuntimeError, 'mock launch failure'):
                self.begin(self.dto)
        self.assertFalse(self.boundary_active)

    def test_ordinary_export_does_not_load_native_rigging(self):
        del self.rig['character_designer_rigging_session_v1']
        self.native.current = lambda *_: (_ for _ in ()).throw(AssertionError('legacy native branch'))
        with patch.object(self.export, '_begin_export', return_value={'ordinary': True}) as launch:
            self.assertEqual(self.begin(), {'ordinary': True})
        self.assertIsNone(launch.call_args.kwargs['rigging_export'])

    def test_link_freezes_complete_packet_and_work_identity_before_isolation(self):
        self.rig[self.link.ACTION_KEY] = self.base
        saved = dict(identity=dict(sourcePackageSha256='D' * 64,
            workItemId=str(uuid.UUID('1' * 32))))

        def capture(_context, session, **identity):
            self.assertFalse(self.boundary_active)
            self.assertIs(session, self.session)
            self.assertEqual(identity, dict(source_packet_sha256='d' * 64, work_item_id='1' * 32,
                source_action=self.base.name, source_action_slot=7))
            return self.dto

        def launch(*_args, **kwargs):
            self.assertTrue(self.boundary_active)
            self.assertEqual(kwargs['rigging_export'], self.dto)
            return {'frozen': True}

        with patch.object(self.link, 'get_link', return_value=saved), \
                patch.object(self.native, 'capture_inputs', side_effect=capture, create=True), \
                patch.object(self.link, '_begin_linked_export', side_effect=launch):
            self.assertEqual(self.link.begin_linked_export(None, self.rig), {'frozen': True})
        self.assertFalse(self.boundary_active)

    def test_link_rejects_readonly_or_foreign_work_before_capture(self):
        self.rig[self.link.ACTION_KEY] = self.base
        saved = dict(identity=dict(sourcePackageSha256='d' * 64, workItemId='3' * 32))
        with patch.object(self.link, 'get_link', return_value=saved), \
                patch.object(self.native, 'capture_inputs', create=True) as capture, \
                patch.object(self.link, '_begin_linked_export') as launch:
            with self.assertRaisesRegex(ValueError, 'Worklist source'):
                self.link.begin_linked_export(None, self.rig)
            saved['identity']['workItemId'] = '1' * 32
            self.session['source_read_only'] = True
            with self.assertRaisesRegex(ValueError, 'read-only'):
                self.link.begin_linked_export(None, self.rig)
            capture.assert_not_called()
            launch.assert_not_called()
        self.assertFalse(self.boundary_active)

    def test_link_start_failure_restores_base_boundary(self):
        self.rig[self.link.ACTION_KEY] = self.base
        saved = dict(identity=dict(sourcePackageSha256='d' * 64, workItemId='1' * 32))
        with patch.object(self.link, 'get_link', return_value=saved), \
                patch.object(self.native, 'capture_inputs', return_value=self.dto, create=True), \
                patch.object(self.link, '_begin_linked_export', side_effect=RuntimeError('mock Link failure')):
            with self.assertRaisesRegex(RuntimeError, 'mock Link failure'):
                self.link.begin_linked_export(None, self.rig)
        self.assertFalse(self.boundary_active)

    def test_published_sidecar_contains_frozen_local_inputs_only(self):
        with tempfile.TemporaryDirectory(prefix='rigging-sidecar-') as directory:
            root = Path(directory)
            stage = root / 'stage'
            stage.mkdir()
            (stage / 'base.fbx').write_bytes(b'fixed test base bytes')
            job = dict(destination=root / 'base.fbx', metadata=root / 'base.animation.json', stage=stage,
                source_blend='owned-test.blend', action=self.base.name, action_slot=7,
                source_package='base.cdanim.json',
                source_package_sha256='d' * 64, rigging_export=self.dto)
            self.export._publish(job, {'ok': True})
            sidecar = json.loads(job['metadata'].read_text(encoding='utf-8'))
            self.assertEqual(sidecar['rigging'], self.dto)
            self.assertEqual(sidecar['channels'], 'skeletal-only')
            self.assertEqual(sidecar['actionSlot'], sidecar['rigging']['sourceActionSlot'])
            self.assertEqual(set(sidecar['rigging']['frames'][0]['controls'][0]),
                {'targetPosition', 'targetRotation', 'hintPosition'})

    def test_publication_rejects_mismatched_frozen_base_slot(self):
        with tempfile.TemporaryDirectory(prefix='rigging-slot-') as directory:
            root = Path(directory)
            stage = root / 'stage'
            stage.mkdir()
            (stage / 'base.fbx').write_bytes(b'fixed test base bytes')
            job = dict(destination=root / 'base.fbx', metadata=root / 'base.animation.json', stage=stage,
                source_blend='owned-test.blend', action=self.base.name, action_slot=9,
                source_package='base.cdanim.json', source_package_sha256='d' * 64, rigging_export=self.dto)
            with self.assertRaisesRegex(ValueError, 'base Action slot'):
                self.export._publish(job, {'ok': True})
            self.assertFalse(job['destination'].exists())
            self.assertFalse(job['metadata'].exists())


if __name__ == '__main__':
    unittest.main()
