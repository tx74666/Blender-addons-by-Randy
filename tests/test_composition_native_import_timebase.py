"""Composition packet clocks execute canonical helpers with Blender boundaries mocked."""
import ast
import base64
from copy import deepcopy
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


def functions(filename, names, environment):
    source = SOURCE / filename
    if filename == 'animation_export.py' and os.environ.get('CHARACTER_DESIGNER_TEST_ANIMATION_EXPORT_SOURCE'):
        source = Path(os.environ['CHARACTER_DESIGNER_TEST_ANIMATION_EXPORT_SOURCE'])
    tree = ast.parse(source.read_text(encoding='utf-8'))
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in selected} != set(names):
        raise AssertionError('Missing canonical function in test admission.')
    exec(compile(ast.Module(body=selected, type_ignores=[]), filename, 'exec'), environment)
    return environment


def constants(filename):
    result = {}
    for node in ast.parse((SOURCE / filename).read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    result[target.id] = node.value.value
    return result


class Action(dict):
    __hash__ = object.__hash__
    library = None
    use_fake_user = False


class Rig(dict):
    __hash__ = object.__hash__
    def animation_data_create(self):
        if self.animation_data is None:
            self.animation_data = NS()
        return self.animation_data


class NativeImportTimebaseTests(unittest.TestCase):
    def setUp(self):
        self.error = type('UnityAnimationError', (ValueError,), {})
        self.ua = functions('unity_animation.py',
            {'_import_sample_frames', '_import_package_action'},
            dict(math=math, json=json, UnityAnimationError=self.error,
                 **constants('unity_animation.py')))
        self.frames = self.ua['_import_sample_frames']
        self.packet = dict(sampleRate=60., duration=2., _path='actual.cdanim.json',
            _sha256='a'*64, frames=[dict(time=0.), dict(time=.137), dict(time=2.)])

    def test_native_60_packet_on_30_scene_preserves_source_seconds(self):
        actual = self.frames(self.packet, 1., 30., native_timebase=True)
        self.assertEqual(actual, [1., 9.22, 121.])
        for frame, sample in zip(actual, self.packet['frames']):
            self.assertAlmostEqual((frame - 1.) / 60., sample['time'], places=14)

    def test_native_30_packet_on_60_scene(self):
        self.packet['sampleRate'] = 30.
        self.assertEqual(self.frames(self.packet, 1., 60., native_timebase=True),
                         [1., 5.11, 61.])

    def test_default_preview_retains_scene_clock_and_start_offset(self):
        self.assertEqual(self.frames(self.packet, 7., 30.), [7., 11.11, 67.])

    def test_fractional_scene_default_and_native_clocks(self):
        scene_rate = 30000. / 1001.
        ordinary = self.frames(self.packet, 1., scene_rate)
        self.assertAlmostEqual(ordinary[-1], 1. + 2. * scene_rate)
        self.assertEqual(self.frames(self.packet, 1., scene_rate, native_timebase=True)[-1], 121.)

    def test_native_invalid_rates_reject_before_blender_access(self):
        self.ua['active_preview'] = Mock(side_effect=AssertionError('Blender accessed'))
        for rate in (True, False, None, '60', 0, .999, 240.001, math.nan, math.inf, -math.inf):
            with self.subTest(rate=rate):
                self.packet['sampleRate'] = rate
                with self.assertRaises(self.error):
                    self.ua['_import_package_action'](None, None, self.packet, native_timebase=True)
        self.ua['active_preview'].assert_not_called()

    def test_timebase_option_is_an_explicit_bool(self):
        for option in (None, 0, 1, 'true'):
            with self.subTest(option=option), self.assertRaises(self.error):
                self.ua['_import_package_action'](None, None, self.packet, native_timebase=option)

    def import_action(self, scene_rate, packet_rate, native, scene_base=1.):
        package_name = '_native_clock_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = []
        original = ModuleType(package_name + '.body_original_mode')
        original.active = Mock(return_value=False)
        controls = ModuleType(package_name + '.unity_animation_controls')
        controls.bake_channels = Mock(return_value=NS(
            channels={('pose.bones["Chest"].location', 0): [0., .2, .8]},
            needs_joint_translation=False))
        self.packet['sampleRate'] = packet_rate
        packet_before = deepcopy(self.packet)
        scene = NS(render=NS(fps=scene_rate, fps_base=scene_base), unit_settings=NS(scale_length=1.))
        target = Rig()
        target.name, target.type = 'Rig', 'ARMATURE'
        target.data, target.pose = NS(bones={}), NS(bones={})
        target.animation_data = None
        target.matrix_world = NS(inverted=lambda: None,
            to_3x3=lambda: NS(col=[NS(length=1.)]))
        scene.objects = {'Rig': target}
        context = NS(object=None, scene=scene, view_layer=NS(update=Mock()))
        action, writes = Action(), []
        self.ua.update(__package__=package_name, active_preview=Mock(return_value=False),
            _mapping=Mock(return_value=NS(indices={}, controls=NS(delegated_names=frozenset()), error_metres=0.)),
            _snapshot=Mock(return_value=dict(fps=scene_rate, fps_base=scene_base, control_properties={})),
            _world_samples=Mock(return_value=[]),
            bpy=NS(data=NS(actions=NS(new=Mock(return_value=action), remove=Mock()))),
            _new_channelbag=Mock(return_value=(NS(handle=3), None)),
            _write_curve=lambda _bag, path, index, frames, values: writes.append(
                (path, index, list(frames), list(values))),
            _set_playing=Mock(), _set_frame=Mock(), PreviewResult=lambda *args: NS(values=args))
        with patch.dict(sys.modules, {package_name: package, original.__name__: original,
                                     controls.__name__: controls}):
            result = self.ua['_import_package_action'](
                context, target, self.packet, native_timebase=native)
        self.assertIs(result.values[0], action)
        self.assertEqual((scene.render.fps, scene.render.fps_base), (scene_rate, scene_base))
        self.assertEqual(self.packet, packet_before)
        self.assertEqual(action['unity_sample_rate'], packet_rate)
        return writes, action

    def test_actual_importer_writes_native_frame_x_and_only_native_provenance(self):
        for scene_rate, scene_base, packet_rate in ((24, 1., 60.), (30, 1., 60.),
                                                    (60, 1., 30.), (30000, 1001., 60.)):
            with self.subTest(scene_rate=scene_rate, scene_base=scene_base, packet_rate=packet_rate):
                writes, action = self.import_action(scene_rate, packet_rate, True, scene_base)
                self.assertEqual(writes[0][2], [1., 1. + .137*packet_rate, 1. + 2.*packet_rate])
                self.assertEqual(action[self.ua['NATIVE_TIMEBASE_KEY']], packet_rate)
        ordinary, preview = self.import_action(30, 60., False)
        self.assertEqual(ordinary[0][2], [1., 5.11, 61.])
        self.assertNotIn(self.ua['NATIVE_TIMEBASE_KEY'], preview)
        fractional, preview = self.import_action(30000, 60., False, 1001.)
        self.assertAlmostEqual(fractional[0][2][-1], 1. + 2.*30000./1001.)
        self.assertNotIn(self.ua['NATIVE_TIMEBASE_KEY'], preview)

    def source(self, owner):
        environment = dict(inspect=inspect, unity_animation=owner,
                           _error=lambda message: self.error(message))
        return functions('animation_link_source.py', {'_import_action'}, environment)['_import_action']

    def test_source_native_rejects_old_or_unknown_cached_owner_without_fallback(self):
        def old(_context, _rig, _packet, *, start_frame=1):
            raise AssertionError('Old owner executed')
        for importer in (None, old, lambda *args, **kwargs: None):
            owner = NS(_import_package_action=importer, import_test_action=Mock())
            with self.subTest(importer=importer), self.assertRaises(self.error):
                self.source(owner)(None, None, self.packet, 1, native_timebase=True)
            owner.import_test_action.assert_not_called()

    def test_source_default_old_call_and_legacy_fallback_stay_compatible(self):
        old = Mock(return_value='ordinary')
        owner = NS(_import_package_action=old, import_test_action=Mock())
        self.assertEqual(self.source(owner)('C', 'R', self.packet, 4), 'ordinary')
        old.assert_called_once_with('C', 'R', self.packet, start_frame=4)
        owner._import_package_action = None
        owner.import_test_action.return_value = 'legacy'
        self.assertEqual(self.source(owner)('C', 'R', self.packet, 4), 'legacy')
        owner.import_test_action.assert_called_once_with('C', 'R', self.packet['_path'], start_frame=4)

    def test_source_native_passes_same_packet_and_propagates_primary_error(self):
        calls = []
        failure = RuntimeError('canonical failure')
        def importer(context, rig, packet, *, start_frame=1, native_timebase=False):
            calls.append((context, rig, packet, start_frame, native_timebase))
            raise failure
        owner = NS(_import_package_action=importer, import_test_action=Mock())
        with self.assertRaises(RuntimeError) as caught:
            self.source(owner)('C', 'R', self.packet, 1, native_timebase=True)
        self.assertIs(caught.exception, failure)
        self.assertIs(calls[0][2], self.packet)
        self.assertTrue(calls[0][4])
        owner.import_test_action.assert_not_called()

    def test_only_composition_b_and_reference_imports_opt_in(self):
        tree = ast.parse((SOURCE / 'animation_composition.py').read_text(encoding='utf-8'))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == '_import_action']
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(any(key.arg == 'native_timebase' and isinstance(key.value, ast.Constant)
                                and key.value.value is True for key in call.keywords) for call in calls))


class NativeCacheAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cd-native-cache-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.cache = self.folder / 'reference.blend'
        self.cache.write_bytes(b'owned reference-only library')
        self.packet = dict(_path=str(self.folder / 'B.cdanim.json'), _sha256='a'*64, sampleRate=60.)
        self.ua = NS(NATIVE_TIMEBASE_KEY=constants('unity_animation.py')['NATIVE_TIMEBASE_KEY'],
                     load_package=Mock(return_value=self.packet))
        self.links = NS(PACKAGE_KEY='packet', PACKAGE_HASH_KEY='packet_sha',
                        _sha256=lambda path: hashlib.sha256(path.read_bytes()).hexdigest())
        self.rig = {}
        self.services = {'unity_animation': self.ua, 'animation_link': self.links,
            'animation_worklist': NS(state=lambda _context: NS(rig=self.rig))}
        self.spec = dict(editNewActionId='', referencePacketSha256='b'*64,
                         referencePacketFile=str(self.folder / 'A.cdanim.json'))
        self.action = self.native_action()
        self.action.library = NS(filepath=str(self.cache))
        self.actions = [self.action]
        self.scope_validation = Mock()
        self.environment = functions('animation_composition.py',
            {'_require_native_action_timebase', '_packet_for_item', 'reference_receipt_key',
             'reference_receipt', 'find_reference'},
            dict(math=math, base64=base64, json=json, Path=Path,
                **constants('animation_composition.py'),
                _host=lambda name: self.services[name],
                _service=lambda _name: NS(validate_packet_composition=self.scope_validation),
                item_spec=lambda _item: self.spec, contract=NS(validate=lambda value: value),
                bpy=NS(data=NS(actions=self.actions), path=NS(abspath=lambda path: path)),
                _motion_hash=lambda _action, slot: 'c'*64 if slot == 3 else 'wrong'))
        self.proof = dict(schema='cdesigner.composition-reference/1',
            packet_path=self.spec['referencePacketFile'], packet_sha256='b'*64,
            slot=3, motion_sha256='c'*64, native_timebase=True, native_sample_rate=60.)
        self.publish_proof()

    def native_action(self):
        return Action({self.ua.NATIVE_TIMEBASE_KEY: 60., 'unity_sample_rate': 60.,
                       'unity_start_frame': 1., 'packet': self.packet['_path'], 'packet_sha': 'a'*64})

    def publish_proof(self):
        self.action[self.environment['REFERENCE_OWNER']] = json.dumps(self.proof)
        receipt = dict(self.proof, cache=str(self.cache), cache_sha256=self.links._sha256(self.cache))
        key = self.environment['reference_receipt_key']('b'*64)
        self.rig[self.environment['REFERENCE_OWNER']] = {key: json.dumps(receipt)}

    def test_exact_native_a_reference_reuses_complete_cache_proof(self):
        self.assertEqual(self.environment['find_reference'](None, self.spec), (self.action, 3))

    def test_unproved_old_a_cache_rejects_without_replacement(self):
        self.proof.pop('native_timebase')
        self.proof.pop('native_sample_rate')
        self.publish_proof()
        before = deepcopy(self.rig)
        with self.assertRaises(ValueError):
            self.environment['find_reference'](None, self.spec)
        self.assertEqual(self.rig, before)
        self.assertEqual(len(self.actions), 1)

    def test_a_marker_and_rate_tamper_reject(self):
        for value in (None, True, 30., math.nan):
            with self.subTest(value=value):
                self.action[self.ua.NATIVE_TIMEBASE_KEY] = value
                with self.assertRaises(ValueError):
                    self.environment['find_reference'](None, self.spec)

    def test_prepared_source_requires_both_custom_and_source_clock_proofs(self):
        item = NS(custom_action=self.native_action(), source_action=self.native_action())
        self.assertIs(self.environment['_packet_for_item'](item), self.packet)
        for which in ('custom_action', 'source_action'):
            with self.subTest(which=which):
                old = getattr(item, which).pop(self.ua.NATIVE_TIMEBASE_KEY)
                before = deepcopy(getattr(item, which))
                with self.assertRaises(ValueError):
                    self.environment['_packet_for_item'](item)
                self.assertEqual(getattr(item, which), before)
                getattr(item, which)[self.ua.NATIVE_TIMEBASE_KEY] = old

    def test_constant_or_cropped_old_source_cannot_hide_missing_clock_proof(self):
        for mode in ('HoldPose', 'HoldLoop'):
            with self.subTest(mode=mode):
                self.spec.update(timeMode=mode, sampleStart=0., sampleEnd=.01)
                custom = self.native_action()
                custom.pop(self.ua.NATIVE_TIMEBASE_KEY)
                with self.assertRaises(ValueError):
                    self.environment['_packet_for_item'](NS(custom_action=custom,
                                                            source_action=self.native_action()))

    def test_genuine_new_action_keeps_existing_timing_admission_route(self):
        self.spec['editNewActionId'] = '1'*32
        custom = self.native_action()
        custom.pop(self.ua.NATIVE_TIMEBASE_KEY)
        self.assertIs(self.environment['_packet_for_item'](
            NS(custom_action=custom, source_action=None)), self.packet)
        self.scope_validation.assert_called_once_with(self.spec, self.packet)


class ExportNativeClockTests(unittest.TestCase):
    def setUp(self):
        self.error = type('UnityAnimationError', (ValueError,), {})
        self.ua = functions('unity_animation.py', {'native_action_frame_rate'},
            dict(math=math, UnityAnimationError=self.error, **constants('unity_animation.py')))
        self.packet = dict(_path='prepared.cdanim.json', _sha256='a'*64, sampleRate=60., duration=1.)
        self.loader = Mock(return_value=self.packet)
        self.ua['load_package'] = self.loader
        self.action = Action({self.ua['NATIVE_TIMEBASE_KEY']: 60., self.ua['PACKAGE_KEY']: self.packet['_path'],
            self.ua['PACKAGE_HASH_KEY']: 'a'*64, 'unity_sample_rate': 60.,
            'unity_start_frame': 1., 'unity_duration': 1.})
        self.action.name = 'Protected A · Work Copy'

    def export_job(self, scene_fps, fps_base, action, end=61.):
        temporary = tempfile.TemporaryDirectory(prefix='cd-native-export-')
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name) / 'A.fbx'
        package_name = '_export_native_clock_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = []
        host = ModuleType(package_name + '.unity_animation')
        host.native_action_frame_rate = self.ua['native_action_frame_rate']
        unity_export = ModuleType(package_name + '.unity_export')
        unity_export._ACTIVE_JOB = None
        guard = ModuleType(package_name + '.dress_export_guard')
        guard.animation_host_admission = Mock(return_value={})
        guard.bind_animation_snapshot = Mock(return_value={})
        guard.JOB_KEY = 'test_dress_admission'
        surfaces = ModuleType(package_name + '.dress_export_snapshot')
        surfaces.capture_animation_surfaces = Mock(return_value=[])
        surfaces.snapshot_scene_roots = Mock(return_value=set())
        worker = ModuleType(package_name + '.animation_export_worker')
        worker._omitted_channels = Mock(return_value=[])
        skeleton = ModuleType(package_name + '.unity_export_worker')
        skeleton._is_control = Mock(return_value=False)
        hair = ModuleType(package_name + '.hair_wiggle_adapter')
        hair.status = Mock(return_value={})
        rig = Rig()
        rig.name, rig.type, rig.mode = 'Rig', 'ARMATURE', 'POSE'
        rig.data = NS(bones=[])
        action.slots = [NS(handle=3, target_id_type='OBJECT')]
        rig.animation_data = NS(action=action, action_slot=action.slots[0], use_tweak_mode=False)
        scene = NS(render=NS(fps=scene_fps, fps_base=fps_base), objects={'Rig': rig},
                   unit_settings=NS(scale_length=1.))
        popen = Mock(return_value=NS(pid=0))  # Native/process creation is never executed.
        libraries_write = Mock()
        environment = functions('animation_export.py', {'_action_frame_clock', '_slot', 'begin_export'},
            dict(__package__=package_name, __file__=str(SOURCE / 'animation_export.py'),
                math=math, Path=Path, tempfile=tempfile, time=time, json=json, _ACTIVE_JOB=None,
                AnimationExportError=type('AnimationExportError', (ValueError,), {}),
                _filename=lambda name, _rig: name, _dispose=Mock(),
                subprocess=NS(Popen=popen, STDOUT=-2),
                bpy=NS(path=NS(abspath=lambda path: path), app=NS(binary_path='never-launched.exe'),
                       data=NS(objects=[], filepath='protected-artist.blend',
                               libraries=NS(write=libraries_write)))))
        modules = {package_name: package}
        modules.update({module.__name__: module for module in
                        (host, unity_export, guard, surfaces, worker, skeleton, hair)})
        try:
            with patch.dict(sys.modules, modules):
                job = environment['begin_export'](NS(scene=scene), rig, action, destination,
                                                  frame_start=1., frame_end=end)
        except Exception:
            popen.assert_not_called()
            libraries_write.assert_not_called()
            guard.animation_host_admission.assert_not_called()
            raise
        self.addCleanup(job['temporary'].cleanup)
        self.addCleanup(job['log'].close)
        actual = json.loads((job['root'] / 'job.json').read_text(encoding='utf-8'))
        popen.assert_called_once()
        self.assertEqual((scene.render.fps, scene.render.fps_base), (scene_fps, fps_base))
        return actual

    def test_native_a_copy_closed_on_24_scene_writes_60_clock_job_one_second(self):
        job = self.export_job(24, 1., self.action)
        self.assertEqual((job['fps'], job['fps_base'], job['sample_rate']), (60, 1., 60.))
        self.assertEqual((job['frame_end'] - job['frame_start']) / (job['fps'] / job['fps_base']), 1.)
        self.loader.assert_called_once_with(self.packet['_path'])

    def test_unmarked_ordinary_action_keeps_scene_clock_despite_unity_metadata(self):
        self.action.pop(self.ua['NATIVE_TIMEBASE_KEY'])
        job = self.export_job(24, 1., self.action)
        self.assertEqual((job['fps'], job['fps_base'], job['sample_rate']), (24, 1., 24.))
        self.assertEqual((job['frame_end'] - job['frame_start']) / 24., 2.5)
        self.loader.assert_not_called()

    def test_native_30_on_60_scene_and_native_fractional_clock(self):
        for packet_rate, scene_rate in ((30., 60), (30000. / 1001., 24), (60., 30)):
            with self.subTest(packet_rate=packet_rate, scene_rate=scene_rate):
                self.packet['sampleRate'] = packet_rate
                self.action[self.ua['NATIVE_TIMEBASE_KEY']] = packet_rate
                self.action['unity_sample_rate'] = packet_rate
                job = self.export_job(scene_rate, 1., self.action, end=1. + packet_rate)
                self.assertIsInstance(job['fps'], int)
                self.assertAlmostEqual(job['fps'] / job['fps_base'], packet_rate)
                self.assertAlmostEqual((job['frame_end'] - 1.) / (job['fps'] / job['fps_base']), 1.)

    def test_fractional_scene_default_is_preserved_and_native_is_independent(self):
        job = self.export_job(30000, 1001., self.action)
        self.assertEqual((job['fps'], job['fps_base']), (60, 1.))
        self.action.pop(self.ua['NATIVE_TIMEBASE_KEY'])
        ordinary = self.export_job(30000, 1001., self.action)
        self.assertEqual((ordinary['fps'], ordinary['fps_base']), (30000, 1001.))

    def test_bad_native_marker_rejects_before_export_mutations(self):
        for value in (True, False, None, 0, 241, math.nan, math.inf):
            with self.subTest(value=value):
                self.action[self.ua['NATIVE_TIMEBASE_KEY']] = value
                with self.assertRaises(ValueError):
                    self.export_job(24, 1., self.action)

    def test_native_packet_digest_rate_duration_origin_tamper_rejects(self):
        for field, value in ((self.ua['PACKAGE_HASH_KEY'], 'b'*64),
                             (self.ua['PACKAGE_KEY'], ''), ('unity_sample_rate', 30.),
                             ('unity_duration', 2.), ('unity_start_frame', True)):
            with self.subTest(field=field):
                prior = self.action[field]
                self.action[field] = value
                with self.assertRaises(self.error):
                    self.ua['native_action_frame_rate'](self.action)
                self.action[field] = prior

    def enter_admission(self, *, marked, explicit):
        class ProofComplete(Exception):
            pass
        self.packet.update(clipGuid='b'*32, clipLocalId=42)
        if not marked:
            self.action.pop(self.ua['NATIVE_TIMEBASE_KEY'])
        spec = dict(referencePacketFile=self.packet['_path'], referencePacketSha256='a'*64,
                    baseClipGuid='b'*32, baseClipLocalId=42)
        rig = Rig()
        rig.mode, rig.animation_data = 'POSE', NS(use_tweak_mode=False)
        item = NS(rig=rig, custom_action=Action(), custom_slot=3)
        worklist = NS(_idle=Mock(), state=Mock(return_value=NS()),
                      _item=Mock(return_value=item), _check_baseline=Mock())
        ua = NS(load_package=self.loader, native_action_frame_rate=self.ua['native_action_frame_rate'],
                _mapping=Mock())
        services = {'animation_worklist': worklist, 'unity_animation': ua,
            'animation_worklist_target': NS(ensure_active=Mock(), is_adopted=Mock(return_value=True)),
            'animation_link': NS(PACKAGE_KEY=self.ua['PACKAGE_KEY'], PACKAGE_HASH_KEY=self.ua['PACKAGE_HASH_KEY'])}
        context = NS(mode='POSE', scene=NS(objects=[rig], unit_settings=NS(scale_length=1.)),
                     view_layer=NS(update=Mock()))
        prior = Mock(side_effect=ProofComplete('admission completed before authoring'))
        edit = Mock(side_effect=AssertionError('Action mutation before clock proof'))
        environment = functions('animation_composition.py', {'enter'},
            dict(Path=Path, uuid=uuid, _host=lambda name: services[name],
                contract=NS(validate_item=lambda value, native_item: value), item_spec=lambda native_item: spec,
                active=Mock(return_value=False), _bag=Mock(), _packet_for_item=Mock(return_value=self.packet),
                prepare_reference=Mock(return_value=(self.action, 3)),
                _service=lambda _name: NS(resolve_scope=Mock(return_value=NS())),
                _following_hold=Mock(return_value=None), _prior=prior, _editing_action=edit))
        before = deepcopy(self.action)
        expected = ProofComplete if marked else ValueError
        with self.assertRaises(expected):
            environment['enter'](context, spec, 'synthetic-item',
                base_action=self.action if explicit else None, base_slot=3 if explicit else None)
        self.assertEqual(self.action, before)
        edit.assert_not_called()
        if marked:
            prior.assert_called_once_with(context, rig)
            context.view_layer.update.assert_called_once()
        else:
            prior.assert_not_called()
            context.view_layer.update.assert_not_called()
            ua._mapping.assert_not_called()

    def test_explicit_unmarked_a_rejects_before_composition_mutation(self):
        self.enter_admission(marked=False, explicit=True)

    def test_explicit_and_found_native_a_share_clock_admission(self):
        self.enter_admission(marked=True, explicit=True)
        self.enter_admission(marked=True, explicit=False)

    def test_actual_reference_and_following_hold_strip_clock_ratios(self):
        tree = ast.parse((SOURCE / 'animation_composition.py').read_text(encoding='utf-8'))
        enter = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'enter')
        expressions = {}
        for node in ast.walk(enter):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in {'aspeed', 'hspeed'}:
                    expressions[node.targets[0].id] = compile(ast.Expression(node.value), 'enter-clock', 'eval')
        for c_rate, a_rate, hold_rate in ((30., 60., 60.), (60., 30., 30.), (30000./1001., 60., 30.)):
            spec = dict(fps=c_rate, referenceSpeed=.8)
            hspec = dict(fps=hold_rate, editSpeed=1.25)
            for kind, rate, speed, phase in (('aspeed', a_rate, .8, .07), ('hspeed', hold_rate, 1.25, .19)):
                strip = NS()
                track = NS(strips=NS(new=lambda *_args: strip))
                env = functions('animation_composition.py', {'_track'},
                    dict(math=math, _slot=lambda _action, slot: slot))
                actual_speed = eval(expressions[kind], dict(spec=spec, hspec=hspec, arate=a_rate))
                env['_track'](NS(nla_tracks=NS(new=lambda: track)), kind, self.action, 3,
                              1., 1. + 2. * rate, speed=actual_speed, phase=phase)
                seconds = .137
                action_frame = 1. + ((1. + seconds*c_rate) - strip.frame_start) / strip.scale
                self.assertAlmostEqual((action_frame - 1.) / rate, phase*2. + speed*seconds, places=12)


if __name__ == '__main__':
    unittest.main()
