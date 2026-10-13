"""Portable Original20 boundaries; no solver or full native proof is simulated.

These tests execute selected actual product functions, plus the real pure
wire validator. Native admission is separately required in isolated Blender.
"""
import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / 'addons' / 'character_designer' / 'unity_animation_original_inputs.py'


def functions(*names, **extra):
    tree = ast.parse(PRODUCT.read_text(encoding='utf-8-sig'))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(selected) != len(names): raise AssertionError('A tested production entry is missing.')
    space = dict(math=math, struct=struct, json=json, hashlib=hashlib, Path=Path,
        PUBLICATION_SCHEMA='cdesigner.controls-only-publication/1', **extra)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(PRODUCT), 'exec'), space)
    return space


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def wire():
    path = ROOT / 'addons' / 'character_designer' / 'animation_rigging_contract.py'
    spec = importlib.util.spec_from_file_location('original20_pure_wire', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class NativeId:
    def __init__(self, name, pointer): self.name, self.pointer = name, pointer
    def as_pointer(self): return self.pointer


def packets():
    identity = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]
    old = dict(schema='packet', units='meters', coordinate='unity-left-handed', matrixLayout='row-major',
        targetGuid='12' * 16, targetAssetPath='Assets/Cosha.prefab', projectId='34' * 16, duration=0.9666666984558105,
        bones=[dict(name='root', path='root', parent=-1, humanRole='', weighted=False, restSource='hierarchy', rest=identity),
               dict(name='tip', path='root/tip', parent=0, humanRole='RightHand', weighted=True, restSource='bindpose', rest=list(identity))])
    new = deepcopy(old); new['duration'] = 0.9666667
    old['bones'][1]['rest'][0] = 0.7823156714439392
    new['bones'][1]['rest'][0] = 0.7823157
    return old, new


def capture_fixture(contract):
    capture = dict(schema=contract.SCHEMA, targetGuid='12' * 16, targetDependencyHash='23' * 16,
        newActionId='db32bc9214884033a9185a8706ec01bc', sourceMode='prefab-default', bindingsSha256='',
        duration=0.9666667, sampleRate=60, loopTime=True, previewEnabled=True, sourceTimeMode='normalized-base-phase',
        targetLayoutSha256='ab' * 32, baseControllerGuid='34' * 16, baseControllerDependencyHash='45' * 16,
        avatarDependencyHash='56' * 16)
    capture['bindings'] = []
    for side in ('Right', 'Left'):
        capture['bindings'].append(dict(rigPath='CoshaRig/Rig1', rigWeight=0., constraintPath='CoshaRig/Rig1/' + side + 'Arm',
            rootPath='CoshaRig/' + side + 'Upper',
            midPath='CoshaRig/' + side + 'Upper/' + side + 'Mid',
            tipPath='CoshaRig/' + side + 'Upper/' + side + 'Mid/' + side + 'Hand',
            targetPath='CoshaRig/Rig1/' + side + 'Target', targetParentPath='CoshaRig/Rig1',
            hintPath='CoshaRig/Rig1/' + side + 'Hint', hintParentPath='CoshaRig/Rig1', constraintWeight=1.,
            targetPositionWeight=1., targetRotationWeight=1., hintWeight=1.,
            maintainTargetPositionOffset=False, maintainTargetRotationOffset=False))
    capture['bindings'][1]['targetParentPath'] = capture['bindings'][0]['tipPath']
    capture['bindings'][1]['targetPath'] = capture['bindings'][1]['targetParentPath'] + '/LeftTarget'
    capture['bindingsSha256'] = contract.bindings_digest(capture['bindings'])
    times = contract._clock(capture)[3]
    matrix = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]
    capture['frames'] = [dict(time=time, controls=[dict(targetPosition=[0., 0., 0.], targetRotation=[0., 0., 0., 1.],
        hintPosition=[0., 1., 0.], targetParentRoot=matrix, hintParentRoot=matrix) for _side in range(2)]) for time in times]
    return contract.validate_capture(capture)


class OriginalInputsContractTests(unittest.TestCase):
    def test_float32_lineage_accepts_serialization_rounding_and_rejects_other_ancestry(self):
        code = functions('_require', '_f32', '_lineage', native=SimpleNamespace(_digest=digest))
        old, new = packets()
        self.assertEqual(len(code['_lineage'](old, new)), 64)
        for mutation in ('parent', 'rest', 'targetGuid'):
            altered = deepcopy(new)
            if mutation == 'parent': altered['bones'][1]['parent'] = -1
            elif mutation == 'rest': altered['bones'][1]['rest'][0] += 0.001
            else: altered['targetGuid'] = 'fe' * 16
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): code['_lineage'](old, altered)

    def test_body30_and_inputs60_are_independent_normalized_clocks(self):
        code = functions('_require', '_f32', 'preview_base_timing', validate_reference=lambda session: None)
        body_duration, input_duration = packets()[0]['duration'], packets()[1]['duration']
        self.assertNotEqual(body_duration, input_duration)
        session = dict(capture={'duration': input_duration, 'sampleRate': 60}, scene_clock=[24, 1.], scene_start=10)
        reference = dict(action=NativeId('old Body', 1), slot=7, duration=body_duration, sample_rate=30., start=1.)
        action, slot, timing = code['preview_base_timing'](session, reference)
        self.assertIs(action, reference['action']); self.assertEqual(slot, 7)
        self.assertEqual(timing['end'], 1. + body_duration * 30.)
        middle = 1. + 0.5 * body_duration * 30.
        self.assertAlmostEqual(10. + (middle - timing['begin']) * timing['scale'], 10. + 0.5 * input_duration * 24., places=12)

    def test_role_boundary_refuses_body_as_input_and_base_tweak(self):
        # The native pair proof is not faked as a pass: only this subsequent
        # Action/slot/role boundary is isolated; native pair validation is external.
        pair = dict(rig=NativeId('Rig', 1), scene=NativeId('Scene', 2), source_action=NativeId('Source20', 3), source_slot=7,
            custom_action=NativeId('Custom20', 4), custom_slot=8, capture={}, work_item_id='31' * 16, packet={'sha256': 'ab' * 32})
        session = dict(original_inputs=pair, rig=pair['rig'], scene=pair['scene'], base_action=pair['source_action'], base_slot=7,
            input_action=pair['custom_action'], input_slot=8, capture={}, work_item_id=pair['work_item_id'],
            source_packet_sha256=pair['packet']['sha256'], source_read_only=False, edit_role='RIGGING', tweak_enabled=True)
        code = functions('_require', '_same', 'validate_session', _pair_validate=lambda value, files=False: value)
        self.assertIs(code['validate_session'](session), pair)
        for replacement in ({'base_action': NativeId('old Body', 9)}, {'input_slot': 9}, {'edit_role': 'BASE'}, {'source_read_only': True}):
            wrong = dict(session, **replacement)
            with self.subTest(replacement=replacement), self.assertRaises(ValueError): code['validate_session'](wrong)

    def publication_environment(self):
        contract = wire(); capture = capture_fixture(contract)
        samples = [dict(time=f['time'], controls=[{k: c[k] for k in contract.LOCAL_FIELDS} for c in f['controls']]) for f in capture['frames']]
        dto = contract.build_export(capture, samples, source_packet_sha256='ab' * 32, work_item_id='31' * 16,
            new_action_id=capture['newActionId'], source_action='Source20', source_action_slot=7, target_input_action='Custom20', target_input_action_slot=8)
        pair = dict(capture=capture, source_uuid='9f' * 16, prepared_report={'path': 'prepare.json', 'sha256': 'cd' * 32},
            body_action=NativeId('old Body', 9), body_slot=11, body_source=NativeId('old Source', 10), body_source_slot=12,
            body_clock=30., body_start=1., body_duration=packets()[0]['duration'], clock_certificate={'path': 'clock.json', 'sha256': 'ef' * 32})
        code = functions('_require', '_sha', '_pin', 'publish_inputs', _active=lambda context, rig: {'original_inputs': pair},
            validate_session=lambda session, files=False: pair, capture_inputs=lambda context, session: dto)
        return code, contract, capture, dto, pair

    def test_publication_uses_real_wire14_full_grid_and_distinct_source_target_roles(self):
        code, contract, capture, expected, pair = self.publication_environment()
        with tempfile.TemporaryDirectory() as folder:
            pins = code['publish_inputs'](None, None, Path(folder) / 'controls.json')
            dto = json.loads(Path(pins['return']['path']).read_text(encoding='utf-8'))
            publication = json.loads(Path(pins['publication']['path']).read_text(encoding='utf-8'))
            self.assertEqual(set(dto), set(contract.EXPORT_FIELDS)); self.assertEqual(len(dto), 14)
            self.assertEqual(dto, expected); self.assertEqual([f['time'] for f in dto['frames']], [f['time'] for f in capture['frames']])
            self.assertEqual(dto['frames'][-1]['time'], 0.9666667); self.assertEqual(len(dto['frames']), 59)
            self.assertTrue(all(set(c) == set(contract.LOCAL_FIELDS) for f in dto['frames'] for c in f['controls']))
            self.assertEqual(publication['controlChannelCount'], 20); self.assertTrue(publication['completed'])
            self.assertEqual(publication['sourceActionId'], pair['source_uuid'])
            self.assertEqual(publication['targetInputActionId'], capture['newActionId'])
            self.assertNotEqual(publication['sourceActionId'], publication['targetInputActionId'])
            self.assertTrue(all(publication[k] is True for k in ('sourceReadOnly', 'targetEditable', 'sourceControlsOnly', 'targetControlsOnly')))
            self.assertEqual(publication['returnSha256'], pins['return']['sha256'])

    def test_publication_never_overwrites_and_failed_readback_never_returns_success(self):
        code, _contract, _capture, _dto, _pair = self.publication_environment()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'controls.json'; path.write_bytes(b'protected existing content')
            with self.assertRaises(ValueError): code['publish_inputs'](None, None, path)
            self.assertEqual(path.read_bytes(), b'protected existing content')
            path.unlink(); real_pin = code['_pin']
            def fail_publication_pin(name, digest):
                if Path(name).stem.endswith('.publication'): raise RuntimeError('Injected publication readback failure')
                return real_pin(name, digest)
            with mock.patch.dict(code, _pin=fail_publication_pin), self.assertRaises(RuntimeError): code['publish_inputs'](None, None, path)
            self.assertFalse(path.exists()); self.assertFalse(path.with_name('controls.publication.json').exists())

    def capture_environment(self):
        """Actual public capture/publish + native capture; RNA admission is a fault seam.

        Actual validate_session checks the pair's role/identity binding, while
        the complete native graph/Body proof is explicitly simulated, not QA.
        All eight file pins and the pure Return validator execute unchanged.
        """
        _code, contract, capture, _dto, pair = self.publication_environment()
        temporary = tempfile.TemporaryDirectory(prefix='original20-public-capture-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        file_keys = ('prepared_report', 'clock_certificate', 'snapshot_receipt', 'packet',
                     'legacy_packet', 'manifest', 'source_cache', 'prepared_model')
        contents = {}
        for key in file_keys:
            data = ('owned source ' + key).encode()
            path = root / (key + '.json'); path.write_bytes(data)
            pair[key] = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
            contents[key] = data
        rig, scene, source, custom = (NativeId(name, index) for index, name in
            enumerate(('Rig', 'Scene', 'Source20', 'Custom20'), 1))
        pair.update(rig=rig, scene=scene, source_action=source, source_slot=7,
            custom_action=custom, custom_slot=8, work_item_id='31' * 16)
        session = dict(original_inputs=pair, rig=rig, scene=scene, base_action=source, base_slot=7,
            input_action=custom, input_slot=8, capture=capture, work_item_id=pair['work_item_id'],
            new_action_id=capture['newActionId'], source_packet_sha256=pair['packet']['sha256'],
            source_read_only=False, edit_role='RIGGING', tweak_enabled=True)
        w = SimpleNamespace(root=root, pair=pair, session=session, rig=rig, contract=contract,
            events=[], pins=[], contents=contents, file_keys=file_keys, failure=None, after_capture=None)

        def pair_admission(value, *, files=False):
            w.events.append('pair')
            self.assertIs(value, pair)
            self.assertFalse(files)
            if w.failure is not None: raise w.failure
            return value

        code = functions('_require', '_same', '_sha', '_pin', '_validate_pair_files',
            'validate_session', 'current', '_active', 'capture_inputs', 'publish_inputs',
            _pair_validate=pair_admission)

        def native_validate(value):
            w.events.append('native_validate')
            self.assertIs(value, session)
            code['validate_session'](value)

        def native_current(value):
            w.events.append('native_current')
            self.assertIs(value, rig)
            native_validate(session)
            return session

        samples = {frame['time']: [{key: control[key] for key in contract.LOCAL_FIELDS}
            for control in frame['controls']] for frame in capture['frames']}
        native_path = PRODUCT.with_name('unity_animation_rigging.py')
        selected = [node for node in ast.parse(native_path.read_text(encoding='utf-8')).body
            if isinstance(node, ast.FunctionDef) and node.name in {'capture_inputs', '_require', '_uid'}]
        self.assertEqual(len(selected), 3)
        native_space = dict(bpy=SimpleNamespace(types=SimpleNamespace(Action=NativeId)), uuid=uuid,
            _validate=native_validate, contract=contract,
            _curves=lambda action, slot: {index: object() for index in range(20)},
            _sample=lambda value, time, *, curves: deepcopy(samples[time]))
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(native_path), 'exec'), native_space)

        def native_capture(context, value, **kwargs):
            w.events.append('native_capture')
            result = native_space['capture_inputs'](context, value, **kwargs)
            if w.after_capture is not None: w.after_capture()
            return result

        actual_pin = code['_pin']
        owned_paths = {row['path'] for key, row in pair.items() if key in file_keys}
        def pin(path, digest):
            if str(path) in owned_paths: w.pins.append(str(path))
            return actual_pin(path, digest)
        code.update(native=SimpleNamespace(current=native_current, capture_inputs=native_capture), _pin=pin)
        w.code = code
        w.expected = contract.build_export(capture,
            [dict(time=time, controls=controls) for time, controls in samples.items()],
            source_packet_sha256=pair['packet']['sha256'], work_item_id=pair['work_item_id'],
            new_action_id=capture['newActionId'], source_action=source.name, source_action_slot=7,
            target_input_action=custom.name, target_input_action_slot=8)
        return w

    def test_public_capture_once_per_call_retains_native_pair_all_files_and_wire14(self):
        w = self.capture_environment()
        for _ in range(2):
            actual = w.code['capture_inputs'](None, w.session)
            self.assertEqual(actual, w.expected)
            self.assertEqual(len(actual), 14)
            self.assertEqual(len(actual['frames']), 59)
        self.assertEqual(w.events, ['native_capture', 'native_validate', 'pair'] * 2)
        self.assertEqual(w.pins, [w.pair[key]['path'] for key in w.file_keys] * 2)

    def test_public_publish_keeps_current_then_complete_capture_without_third_pair_scan(self):
        w = self.capture_environment()
        result = w.code['publish_inputs'](None, w.rig, w.root / 'return.json')
        self.assertEqual(w.events, ['native_current', 'native_validate', 'pair',
                                    'native_capture', 'native_validate', 'pair'])
        self.assertEqual(w.pins, [w.pair[key]['path'] for key in w.file_keys] * 2)
        self.assertEqual(json.loads(Path(result['return']['path']).read_text()), w.expected)
        publication = json.loads(Path(result['publication']['path']).read_text())
        self.assertEqual(publication['controlChannelCount'], 20)
        self.assertEqual(publication['returnSha256'], result['return']['sha256'])

    def test_capture_and_publish_still_reject_actual_pair_role_or_slot_mismatch(self):
        for boundary in ('capture', 'publish'):
            for mutation in ('base_action', 'input_slot', 'edit_role'):
                with self.subTest(boundary=boundary, mutation=mutation):
                    w = self.capture_environment()
                    w.session[mutation] = {'base_action': NativeId('foreign Body', 99),
                        'input_slot': 99, 'edit_role': 'BASE'}[mutation]
                    with self.assertRaisesRegex(ValueError, 'Original Body is read-only'):
                        if boundary == 'capture': w.code['capture_inputs'](None, w.session)
                        else: w.code['publish_inputs'](None, w.rig, w.root / 'return.json')
                    self.assertFalse((w.root / 'return.json').exists())
                    self.assertEqual(w.pins, [])

    def test_capture_and_publish_propagate_full_native_pair_failure_without_cached_success(self):
        for boundary in ('capture', 'publish'):
            for reason in ('Body motion changed', 'Source owner changed', 'owned native tracks changed'):
                with self.subTest(boundary=boundary, reason=reason):
                    w = self.capture_environment()
                    w.code['capture_inputs'](None, w.session)
                    w.failure = RuntimeError(reason)
                    with self.assertRaises(RuntimeError) as caught:
                        if boundary == 'capture': w.code['capture_inputs'](None, w.session)
                        else: w.code['publish_inputs'](None, w.rig, w.root / 'return.json')
                    self.assertIs(caught.exception, w.failure)
                    self.assertFalse((w.root / 'return.json').exists())

    def test_capture_and_publish_rehash_each_changed_source_file_after_prior_success(self):
        for boundary in ('capture', 'publish'):
            w = self.capture_environment()
            w.code['capture_inputs'](None, w.session)
            for key in w.file_keys:
                with self.subTest(boundary=boundary, file=key):
                    path = Path(w.pair[key]['path']); path.write_bytes(w.contents[key] + b' changed')
                    try:
                        with self.assertRaisesRegex(ValueError, 'authenticated Original input file changed'):
                            if boundary == 'capture': w.code['capture_inputs'](None, w.session)
                            else: w.code['publish_inputs'](None, w.rig, w.root / 'return.json')
                        self.assertFalse((w.root / 'return.json').exists())
                    finally: path.write_bytes(w.contents[key])

    def test_file_change_during_native_capture_prevents_return_and_publication(self):
        for boundary in ('capture', 'publish'):
            with self.subTest(boundary=boundary):
                w = self.capture_environment()
                path = Path(w.pair['prepared_model']['path'])
                w.after_capture = lambda: path.write_bytes(b'changed before public return')
                with self.assertRaisesRegex(ValueError, 'authenticated Original input file changed'):
                    if boundary == 'capture': w.code['capture_inputs'](None, w.session)
                    else: w.code['publish_inputs'](None, w.rig, w.root / 'return.json')
                self.assertEqual(w.events.count('native_capture'), 1)
                self.assertFalse((w.root / 'return.json').exists())
                self.assertFalse((w.root / 'return.publication.json').exists())


    def metadata_environment(self):
        class Id:
            def __init__(self, name, library=None):
                object.__setattr__(self, 'name', name)
                object.__setattr__(self, 'library', library)
            def __setattr__(self, name, value):
                raise AssertionError('A referenced native ID must remain read-only.')
            def items(self):
                raise AssertionError('Native ID contents must not be traversed or rewritten.')
        class Armature(Id):
            pass
        class Group:
            def __init__(self, values): self.values = values
            def items(self): return self.values.items()
        class Array:
            def __init__(self, values): self.values = values
            def to_list(self): return list(self.values)
        bpy = SimpleNamespace(types=SimpleNamespace(ID=Id, Armature=Armature))
        forbidden = mock.Mock(side_effect=AssertionError('Digest must not use the recovery encoder.'))
        code = functions('_require', '_reference_headers', '_properties_digest', bpy=bpy,
            native=SimpleNamespace(_digest=digest), composition=SimpleNamespace(_encode=forbidden))
        return code, bpy, Armature, Group, Array, forbidden

    def test_body_digest_reads_existing_armature_reference_without_ownership_or_ID_writes(self):
        code, bpy, Armature, Group, Array, forbidden = self.metadata_environment()
        original = Armature('Original Main data')
        metadata = {'old_motion_refs': Group({'data': original, 'values': Array([1., 2., 3.])}),
            'flags': [True, None, 'unchanged']}
        before = (original.name, original.library, list(metadata), metadata['old_motion_refs'].values['data'])
        actual = code['_properties_digest'](metadata)
        self.assertEqual(len(actual), 64)
        self.assertEqual(code['_properties_digest'](metadata), actual)
        self.assertEqual(before, (original.name, original.library, list(metadata), metadata['old_motion_refs'].values['data']))
        forbidden.assert_not_called()
        # Execute the actual unchanged transaction encoder separately: read-only
        # digest acceptance must never promote Armature ownership into recovery.
        recovery = ROOT / 'addons' / 'character_designer' / 'animation_composition.py'
        tree = ast.parse(recovery.read_text(encoding='utf-8-sig'))
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_encode']
        self.assertEqual(len(selected), 1)
        namespace = {'bpy': bpy}
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(recovery), 'exec'), namespace)
        refs = {}
        with self.assertRaisesRegex(ValueError, 'Armature ownership'):
            namespace['_encode']({'original': original}, refs)
        self.assertEqual(refs, {})

    def test_body_digest_changes_for_values_and_ID_headers_and_rejects_unknown_values(self):
        code, _bpy, Armature, Group, Array, forbidden = self.metadata_environment()
        original = Armature('Original Main data')
        baseline = code['_properties_digest']({'data': original, 'value': [1., 2.]})
        for altered in ({'data': original, 'value': [1., 3.]},
                {'data': Armature('Different data'), 'value': [1., 2.]},
                {'data': Armature('Original Main data', SimpleNamespace(filepath='other.blend')), 'value': [1., 2.]},
                {'data': {'id_header': {'type': 'Armature', 'name': original.name, 'library': None}}, 'value': [1., 2.]}):
            with self.subTest(altered=altered):
                self.assertNotEqual(code['_properties_digest'](altered), baseline)
        for invalid in (float('nan'), object()):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                code['_properties_digest']({'invalid': invalid})
        forbidden.assert_not_called()



    def test_start_setup_blocks_actual_handlers_and_restores_busy_before_activation_and_rollback(self):
        # Execute actual _start, actual native callbacks and the official defer
        # function. Native geometry/activation is a declared fake boundary.
        def actual_functions(filename, names, namespace):
            path = ROOT / 'addons' / 'character_designer' / filename
            selected = [node for node in ast.parse(path.read_text(encoding='utf-8')).body
                if isinstance(node, ast.FunctionDef) and node.name in names]
            self.assertEqual({node.name for node in selected}, set(names))
            exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
        for fault in (None, 'setup', 'activate'):
            with self.subTest(fault=fault):
                class Rig(dict):
                    def as_pointer(self): return 41
                rig = Rig(legacy='unchanged')
                scene = SimpleNamespace(objects=[rig], unit_settings=SimpleNamespace(scale_length=1.))
                ctx = SimpleNamespace(scene=scene)
                events, state = [], {'mode': 'FK', 'stores': 0, 'current_calls': 0}
                session = dict(rig=rig, scene=scene, enabled=False, prior={'mode': 'FK'}, display={})
                native = ModuleType('start_test.unity_animation_rigging')
                native._BUSY, native._SUSPENDED, native.SESSION_KEY = {77}, {}, 'session'
                def unexpected_current(_rig):
                    state['current_calls'] += 1
                    raise AssertionError('Setup callbacks must short-circuit before full current/decode/hash.')
                callbacks = dict(bpy=SimpleNamespace(app=SimpleNamespace(handlers=SimpleNamespace(persistent=lambda f: f))),
                    SESSION_KEY=native.SESSION_KEY, _BUSY=native._BUSY, _SUSPENDED=native._SUSPENDED,
                    _HANDLER_ERRORS={}, current=unexpected_current)
                actual_functions('unity_animation_rigging.py', ('_frame', '_depsgraph'), callbacks)
                def fire_callbacks():
                    callbacks['_frame'](scene)
                    callbacks['_depsgraph'](scene, None)
                forearm = ModuleType('start_test.forearm_twist')
                forearm.__dict__.update(contextmanager=contextmanager, _BUSY=False,
                    update_runtime=lambda _scene: events.append(('final_forearm', state['mode'])))
                actual_functions('forearm_twist.py', ('defer_runtime',), forearm.__dict__)
                def stored(_rig, value):
                    self.assertIn(41, native._BUSY)
                    state['stores'] += 1; rig[native.SESSION_KEY] = 'owned'
                    events.append(('store', state['stores'])); fire_callbacks()
                    if fault == 'setup' and state['stores'] == 2:
                        raise RuntimeError('injected second store failure')
                def activated(_ctx, value):
                    self.assertNotIn(41, native._BUSY)
                    self.assertTrue(forearm._BUSY)
                    with forearm.defer_runtime(ctx):
                        events.append(('activate',)); state['mode'] = 'IK'
                        if fault == 'activate': raise RuntimeError('injected activation failure')
                    return value
                def restored(_ctx, _rig):
                    with forearm.defer_runtime(ctx):
                        events.append(('native_restore',)); state['mode'] = 'FK'
                        rig.pop(native.SESSION_KEY, None)
                native._store, native.activate, native.restore_owned = stored, activated, restored
                weapon = ModuleType('start_test.animation_composition_weapon'); weapon.RECORD = 'weapon_record'
                def add_display(*args, **kwargs):
                    self.assertIn(41, native._BUSY)
                    events.append(('weapon_add',)); fire_callbacks(); rig[weapon.RECORD] = 'owned-display'
                    return {'display_owner': 'test-owned'}
                def remove_display(*args, **kwargs):
                    events.append(('weapon_remove',)); rig.pop(weapon.RECORD, None)
                weapon.add_rigging_context, weapon.remove_rigging_context = add_display, remove_display
                parent = ModuleType('start_test'); parent.__path__ = []
                parent.forearm_twist, parent.animation_composition_weapon = forearm, weapon
                def restore_prior(*args):
                    events.append(('prior_restore',)); state['mode'] = 'FK'
                code = functions('_require', '_start', __package__='start_test', __name__='start_test.original_inputs',
                    native=native, _session=lambda *_args: session,
                    ua=SimpleNamespace(load_package=lambda _path: {'packet': 'already authenticated'}, _mapping=lambda *_args: 'validated-mapping'),
                    composition=SimpleNamespace(_restore_prior=restore_prior),
                    bone_display=SimpleNamespace(_restore=lambda *_args: events.append(('display_restore',))))
                pair = dict(rig=rig, capture={'previewEnabled': True, 'weaponPreview': {'authenticated': True}}, packet={'path': 'packet.json'})
                modules = {'start_test': parent, 'start_test.forearm_twist': forearm,
                    'start_test.animation_composition_weapon': weapon}
                with mock.patch.dict(sys.modules, modules):
                    if fault is None:
                        self.assertIs(code['_start'](ctx, pair), session)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'injected'):
                            code['_start'](ctx, pair)
                self.assertEqual(state['current_calls'], 0)
                self.assertEqual(native._BUSY, {77}); self.assertEqual(native._SUSPENDED, {})
                self.assertFalse(forearm._BUSY)
                expected_mode = 'IK' if fault is None else 'FK'
                self.assertEqual([event for event in events if event[0] == 'final_forearm'], [('final_forearm', expected_mode)])
                self.assertEqual(events[-1], ('final_forearm', expected_mode))
                self.assertEqual(rig['legacy'], 'unchanged')
                if fault is not None:
                    self.assertEqual(rig, {'legacy': 'unchanged'})
                    self.assertLess(events.index(('display_restore',)), len(events) - 1)
                # Genuine reentrant/suspended entry is refused before _session
                # or any writer, without clearing another owner's membership.
                for busy, depth in ((True, 0), (False, 1)):
                    if busy: native._BUSY.add(41)
                    native._SUSPENDED[41] = depth
                    with mock.patch.dict(code, _session=mock.Mock(side_effect=AssertionError('must reject before session'))):
                        with self.assertRaisesRegex(ValueError, 'outside an existing'):
                            code['_start'](ctx, pair)
                    self.assertEqual(41 in native._BUSY, busy)
                    native._BUSY.discard(41); native._SUSPENDED.pop(41, None)



if __name__ == '__main__': unittest.main()
