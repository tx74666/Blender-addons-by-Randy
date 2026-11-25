"""Proof parity and ownership across main-thread preparation/verification yields."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def finish(iterator):
    while True:
        try:
            next(iterator)
        except StopIteration as result:
            return result.value


class ID:
    counter = 0
    def __init__(self, name='ID'):
        ID.counter += 1
        self.pointer = ID.counter
        self.name = self.name_full = name
        self.library = None
        self.constraints = []
        self.bl_rna = NS(identifier='Object')
    def as_pointer(self):
        return self.pointer


class Output(dict):
    __hash__ = object.__hash__
    users = 0
    use_fake_user = False
    name = 'Owned result'
    def as_pointer(self):
        return id(self)


class CooperativeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        package = ModuleType('_cooperative_controller_tests')
        package.__path__ = [str(ROOT)]
        self.bpy = ModuleType('bpy')
        handlers = NS(persistent=lambda callback: callback, depsgraph_update_post=[], load_pre=[], undo_pre=[], redo_pre=[])
        self.bpy.app = NS(handlers=handlers)
        self.bpy.types = NS(ID=ID, Bone=type('Bone', (), {}), PoseBone=type('PoseBone', (), {}))
        self.bpy.data = NS(actions=NS(remove=Mock()), libraries=[], objects=[])
        self.bpy.utils = NS(user_resource=lambda *args, **kwargs: self.folder)
        self.mapping = ModuleType(package.__name__ + '.animation_controller_mapping')
        self.mapping._source_fps = lambda source, explicit: float(explicit)
        self.mapping.RECORD = 'mapping'
        self.enterContext(patch.dict(sys.modules, {
            package.__name__: package, 'bpy': self.bpy, self.mapping.__name__: self.mapping}))
        package.animation_controller_mapping = self.mapping
        spec = importlib.util.spec_from_file_location(package.__name__ + '.animation_controller_worker', ROOT / 'animation_controller_worker.py')
        self.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.worker)
        self.fingerprint = __import__(package.__name__ + '.animation_worklist_fingerprint', fromlist=['x'])
        self.source, self.target, self.action = ID('source'), ID('target'), ID('source Action')
        self.source.data, self.target.data = ID('source data'), ID('target data')
        self.scene = NS(objects={'target': self.target})
        self.context = NS(window=object(), scene=self.scene, view_layer=object())
        self.override_depth = 0
        @contextmanager
        def override(**kwargs):
            self.override_depth += 1
            try:
                yield
            finally:
                self.override_depth -= 1
        self.bpy.context = NS(temp_override=override)
        self.inputs = ['unchanged']
        self.worker._light_inputs = Mock(side_effect=lambda job: tuple(self.inputs))
        self.worker._write = Mock()
        self.worker._navigation = Mock(return_value={'scene': 1, 'dirty': False})
        self.action.items = lambda: iter(())
        self.worker._action_capture = Mock(return_value={'settings': 'unchanged'})

    def begin(self, iterator=None):
        if iterator is not None:
            def transaction(job):
                return (yield from iterator(job))
            self.worker._conversion = transaction
        job = self.worker.begin(self.context, self.source, self.action, 12, self.target, source_fps=30.)
        job.update(source_capture={'settings': 'unchanged'}, candidate_capture={'settings': 'unchanged'},
                   candidate_metadata={})
        return job

    def test_begin_returns_before_any_heavy_capture_and_reserves_only_its_owners(self):
        entered = []
        def transaction(job):
            entered.append('heavy capture')
            yield
        self.worker._witness = self.worker._portable = self.worker._action_hash = Mock(side_effect=AssertionError('synchronous capture'))
        self.worker._resources = Mock(side_effect=AssertionError('capture resource phase'))
        with patch.object(self.worker.subprocess, 'Popen') as spawn:
            job = self.begin(transaction)
            self.assertEqual(entered, [])
            self.assertIsNone(job['process'])
            self.assertEqual(job['state'], 'PREPARING')
            self.assertIs(self.worker.conflicting(self.source, object()), job)
            self.assertIsNone(self.worker.conflicting(object(), object()))
            spawn.assert_not_called()
        self.worker.close(job)

    def test_real_transaction_reaches_verified_handoff_without_hidden_sync_capture(self):
        """Exercise the actual transaction phase wiring with native I/O replaced."""
        isolation = ModuleType(self.mapping.__name__.rsplit('.', 1)[0] + '.animation_controller_isolation')
        isolation._graph = lambda rig: None
        isolation._secondary_sources = lambda rig: (None, ())
        self.enterContext(patch.dict(sys.modules, {isolation.__name__: isolation}))
        self.bpy.types.Object = ID
        self.scene = ID('home')
        self.scene.objects = {'target': self.target}
        self.scene.frame_current, self.scene.frame_subframe = 1, 0.
        self.scene.type, self.action.type = 'SCENE', 'ACTION'
        self.context.scene = self.scene
        self.source.type = self.target.type = 'ARMATURE'
        self.bpy.data.objects = [self.source, self.target]
        self.bpy.app.version, self.bpy.app.binary_path = (5, 2, 0), 'owned-blender.exe'
        self.worker._scene_inputs = lambda scene: {'frame': 1., 'fps': 30}
        self.worker._resources = lambda path: {'available_bytes': 4_000_000_000}
        self.worker._parent_identity = lambda: {'pid': 123}
        packet = {'object': {}, 'pose': {}, 'constraints': []}
        proof = {'source_action': 'source hash', 'source_rest': 'source rest', 'target_rest': 'target rest',
                 'source_metadata': self.worker._digest({})}
        def witness(context, source, action, slot, target, raw_out=None, captures=None):
            yield
            if captures is not None:
                captures['values'][(action.as_pointer(), slot)] = {'settings': 'unchanged'}
            if raw_out is not None:
                raw_out.update({rig.name_full: packet for rig in (source, target)})
            return {'portable': proof, 'rigs': [(rig.as_pointer(), rig.data.as_pointer(), rig.name,
                    None, 'rest', self.worker._digest(packet)) for rig in (source, target)]}
        self.worker._witness_steps = witness
        self.worker._raw_packet = lambda rig: packet
        def fresh(*args, **kwargs):
            yield
            return job['fresh_inputs']
        self.worker._fresh_steps = fresh
        self.worker._witness = self.worker._portable = self.worker._action_hash = Mock(side_effect=AssertionError('synchronous full proof'))
        libraries = type('Libraries', (list,), {})()
        libraries.write = lambda path, *args, **kwargs: Path(path).write_bytes(b'owned native snapshot')
        self.bpy.data.libraries = libraries
        self.mapping.MappingResult = lambda action, slot, fingerprint, diagnostics: NS(
            action=action, slot=slot, source_fingerprint=fingerprint, diagnostics=diagnostics)
        output = Output()
        def load(job, receipt):
            job['candidate'], job['candidate_metadata'] = output, {}
            yield
            return output, 12
        self.worker._load_output_steps = load
        def action_hash(*args):
            job['captures']['values'][(output.as_pointer(), 12)] = {'settings': 'unchanged'}
            yield
            return 'candidate hash'
        self.worker._action_hash_steps = action_hash
        def spawn(command, **kwargs):
            request = json.loads((job['root'] / 'job.json').read_text(encoding='utf8'))
            receipt = dict(schema=self.worker.SCHEMA, job=job['root'].name, state='READY',
                input_proof=request['input_proof'], snapshot_sha256=request['snapshot_sha256'],
                output_sha256='output hash', action_sha256='candidate hash',
                diagnostics=dict(native_output_verified=True, source_action_sha256='source hash'))
            (job['root'] / 'result.json').write_text(json.dumps(receipt), encoding='utf8')
            return NS(poll=lambda: 0, returncode=0, pid=456)
        with patch.object(self.worker.subprocess, 'Popen', side_effect=spawn) as launch:
            job = self.begin()
            for _ in range(200):
                status = self.worker.poll(job)
                if status is not None:
                    break
            else:
                self.fail('The actual transaction did not reach its handoff boundary.')
            self.assertEqual(status, {'state': 'EXITED'})
            self.assertEqual(job['fresh_epoch'], job['epoch'])
            result = self.worker.finish(job)
            self.assertIs(result.action, output)
            self.assertTrue(job['handed_off'])
            launch.assert_called_once()
            self.assertIn('resources_at_launch', job['metrics'])

    def test_cancel_before_first_slice_never_enters_capture_or_launch(self):
        entered = []
        def transaction(job):
            entered.append(True)
            yield
        job = self.begin(transaction)
        self.worker.request_cancel(job)
        self.assertEqual(self.worker.poll(job), {'state': 'CANCELLED'})
        self.assertEqual(entered, [])
        self.assertFalse(self.worker._JOBS)
        self.assertFalse(self.bpy.app.handlers.load_pre)
        self.assertFalse(self.bpy.app.handlers.depsgraph_update_post)
        self.worker.close(job)

    def test_cancel_between_slices_preserves_new_artist_input(self):
        def transaction(job):
            yield self.worker._TURN
            raise AssertionError('Must not resume after cancel')
        job = self.begin(transaction)
        self.assertIsNone(self.worker.poll(job))
        self.inputs[:] = ['artist edited while pending']
        self.worker._restore_packet = Mock(side_effect=AssertionError('Never restore artist input'))
        self.worker.request_cancel(job)
        self.assertEqual(self.worker.poll(job)['state'], 'CANCELLED')
        self.assertEqual(self.inputs, ['artist edited while pending'])
        self.worker._restore_packet.assert_not_called()

    def test_stale_binding_after_yield_refuses_without_restore_or_spawn(self):
        def transaction(job):
            yield self.worker._TURN
            raise AssertionError('Old request must not continue')
        job = self.begin(transaction)
        self.worker.poll(job)
        self.inputs[:] = ['new Action binding']
        with self.assertRaises(self.worker.StaleInputs):
            self.worker.poll(job)
        self.assertEqual(self.inputs, ['new Action binding'])
        self.assertTrue(job['closed'])
        self.assertFalse(self.worker._JOBS)

    def test_load_undo_redo_and_explicit_close_release_unlaunched_jobs(self):
        for event in ('load_pre', 'undo_pre', 'redo_pre', 'unregister-close'):
            with self.subTest(event=event):
                job = self.begin(lambda job: iter(()))
                if event == 'unregister-close':
                    self.worker.request_cancel(job)
                    self.worker.close(job)
                else:
                    for callback in tuple(getattr(self.bpy.app.handlers, event)):
                        callback(None)
                self.assertTrue(job['closed'])
                self.assertFalse(self.worker._JOBS)
                self.assertFalse(self.bpy.app.handlers.depsgraph_update_post)

    def test_scheduler_stops_at_budget_and_releases_context_each_tick(self):
        now, steps = [0.], []
        def transaction(job):
            for index in range(20):
                self.assertEqual(self.override_depth, 1)
                steps.append(index)
                now[0] += .011
                yield
        job = self.begin(transaction)
        with patch.object(self.worker.time, 'monotonic', side_effect=lambda: now[0]):
            self.assertIsNone(self.worker.poll(job))
        self.assertEqual(steps, [0, 1])
        self.assertEqual(self.override_depth, 0)
        self.assertAlmostEqual(job['metrics']['max_chunk_seconds'], .011)
        self.worker.close(job)

    def test_native_barrier_yields_before_atomic_operation_and_allows_cancel(self):
        atomic = Mock()
        def transaction(job):
            yield self.worker._TURN
            atomic()
            yield self.worker._TURN
        job = self.begin(transaction)
        self.worker.poll(job)
        atomic.assert_not_called()
        self.worker.request_cancel(job)
        self.worker.poll(job)
        atomic.assert_not_called()

    def test_torn_capture_is_discarded_and_recollected_not_mixed(self):
        job = self.begin(lambda job: iter(()))
        source = [1]
        reads = []
        def capture():
            first = source[0]
            yield
            reads.append((first, source[0]))
            return reads[-1]
        iterator = self.worker._coherent(job, capture)
        next(iterator)
        source[0] = 2
        job['epoch'] += 1
        self.assertEqual(finish(iterator), (2, 2))
        self.assertEqual(reads, [(1, 2), (2, 2)])
        self.assertEqual(job['metrics']['capture_retries'], 1)
        self.worker.close(job)

    def test_continuously_mutating_capture_refuses_boundedly(self):
        job = self.begin(lambda job: iter(()))
        def capture():
            job['epoch'] += 1
            yield
            return 'torn'
        with self.assertRaises(self.worker.StaleInputs):
            finish(self.worker._coherent(job, capture))
        self.assertEqual(job['metrics']['capture_retries'], 3)
        self.worker.close(job)

    def test_irrelevant_native_update_does_not_poison_capture(self):
        job = self.begin(lambda job: iter(()))
        other = ID('unrelated mesh')
        self.worker._capture_updates(None, NS(updates=[NS(id=other)]))
        self.assertEqual(job['epoch'], 0)
        self.worker._capture_updates(None, NS(updates=[NS(id=self.source)]))
        self.assertEqual(job['epoch'], 1)
        self.worker.close(job)

    def test_large_digest_and_streaming_file_write_preserve_exact_json(self):
        payload = {'中文': [{'f': .125, 'z': -0., 'text': 'a\\b\n"'} for _ in range(3000)], 'none': None}
        self.assertEqual(finish(self.worker._digest_steps(payload)), self.worker._digest(payload))
        path = Path(self.folder) / 'request.json'
        finish(self.worker._write_steps(path, payload))
        self.assertEqual(path.read_text(encoding='utf8'), self.worker._json(payload))
        self.assertFalse(path.with_name('request.json.pending').exists())
        self.assertEqual(finish(self.worker._sha_steps(path)), hashlib.sha256(path.read_bytes()).hexdigest())

    def test_partial_request_never_appears_as_complete_job_json(self):
        path = Path(self.folder) / 'job.json'
        iterator = self.worker._write_steps(path, {'raw': ['value' * 50] * 1000})
        next(iterator)
        self.assertFalse(path.exists())
        iterator.close()
        self.assertFalse(path.exists())
        self.assertTrue(path.with_name('job.json.pending').exists())

    def action_payload(self, streamed):
        points = [dict(co=[float(index), .25], handle_left=[index-.2, 0.], handle_right=[index+.3, .5],
                       interpolation='BEZIER', easing='AUTO', type='KEYFRAME') for index in range(100)]
        rows = [dict(data_path='pose.bones["指.L"].location', array_index=0,
                     keyframe_points=self.fingerprint._StreamSequence(iter(points)) if streamed else points,
                     sampled_points=[], modifiers=[dict(type='NOISE', strength=.125)])]
        return dict(schema=self.fingerprint.SCHEMA, action=dict(layers=[dict(strips=[dict(curves=rows)])]))

    def test_cooperative_action_stream_matches_original_exact_proof(self):
        self.assertEqual(finish(self.worker._action_stream_steps(self.action_payload(True))),
                         self.fingerprint._stream_digest(self.action_payload(False)))

    def test_cooperative_action_stream_retains_point_bound_and_nonfinite_refusal(self):
        with patch.object(self.fingerprint, 'MAX_ACTION_POINTS', 50):
            with self.assertRaises(self.fingerprint.FingerprintUnknown):
                finish(self.worker._action_stream_steps(self.action_payload(True)))
        with self.assertRaises(self.fingerprint.FingerprintUnknown):
            finish(self.worker._action_stream_steps({'co': [float('nan')]}))

    def test_source_edit_during_candidate_proof_cannot_hide_behind_new_epoch(self):
        job = self.begin(lambda job: iter(()))
        job['fresh_inputs'] = 'original source'
        job['candidate_metadata'] = {}
        output = Output()
        current, fresh_calls, candidate_calls = ['original source'], [], []
        def fresh(*args, **kwargs):
            fresh_calls.append(current[0])
            yield
            return current[0]
        def candidate(*args):
            candidate_calls.append(True)
            if len(candidate_calls) == 1:
                current[0] = 'artist changed source keys'
                job['epoch'] += 1
            yield
            return 'unchanged candidate hash'
        self.worker._fresh_steps = fresh
        self.worker._action_hash_steps = candidate
        with self.assertRaises(self.worker.StaleInputs):
            finish(self.worker._final_proofs_steps(job, output, 12, 'unchanged candidate hash'))
        self.assertEqual(current[0], 'artist changed source keys')
        self.assertEqual(fresh_calls, ['original source', 'artist changed source keys'])
        self.worker.close(job)

    def test_atomic_native_io_has_separate_timing_and_exact_context_gate(self):
        job = self.begin(lambda job: iter(()))
        state = dict(scene=1, selected=[3], dirty=False)
        self.worker._navigation = lambda ctx: dict(state)
        now = iter((0., 1.25))
        with patch.object(self.worker.time, 'monotonic', side_effect=lambda: next(now)):
            self.assertEqual(self.worker._atomic(job, 'Snapshot', lambda: 'native value'), 'native value')
        self.assertEqual(job['metrics']['atomic_operations'], [{'phase': 'Snapshot', 'seconds': 1.25}])
        def unsafe():
            state['selected'] = [4]
        with self.assertRaisesRegex(RuntimeError, 'original context'):
            self.worker._atomic(job, 'Append', unsafe, appended=True)
        self.worker.close(job)

    def test_context_can_change_between_reads_but_author_proof_stays_exact(self):
        first = dict(scene=1, selected=[1], portable='same proof', rigs=['same raw'])
        later = dict(first, scene=2, selected=[9])
        self.assertTrue(self.worker._author_equal(first, later))
        self.assertFalse(self.worker._author_equal(first, dict(later, rigs=['changed raw'])))
        self.assertFalse(self.worker._author_equal(first, dict(later, portable='changed keys')))

    def test_finish_handoff_and_later_close_never_remove_published_action(self):
        job = self.begin(lambda job: iter(()))
        output = Output()
        job.update(state='READY', ready_epoch=job['epoch'], candidate=output,
                   raw_owners=(), fresh_inputs={'raw': {}, 'portable': {'source_metadata': self.worker._digest({})}},
                   result=NS(action=output, slot=12, diagnostics={'native_output_verified': True}),
                   receipt={'action_sha256': 'verified'})
        (job['root'] / 'inputs.blend').write_bytes(b'owned snapshot')
        result = self.worker.finish(job)
        self.assertIs(result.action, output)
        self.assertTrue(job['handed_off'])
        self.worker.close(job)
        self.bpy.data.actions.remove.assert_not_called()
        self.assertEqual(json.loads(output['mapping']), {'native_output_verified': True})
        self.assertFalse(self.worker._JOBS)

    def test_finish_refuses_direct_raw_edit_without_depsgraph_event(self):
        job = self.begin(lambda job: iter(()))
        output = Output()
        before = {'object': {}, 'pose': {'foot': {'location': [0., 0., 0.]}}, 'constraints': []}
        after = {'object': {}, 'pose': {'foot': {'location': [0., .007, 0.]}}, 'constraints': []}
        job.update(state='READY', ready_epoch=job['epoch'], candidate=output,
                   raw_owners=(self.target,), fresh_inputs={'raw': {self.target.name: self.worker._digest(before)}},
                   result=NS(action=output, slot=12, diagnostics={}), receipt={'action_sha256': 'verified'})
        self.worker._raw_packet = Mock(return_value=after)
        with self.assertRaisesRegex(self.worker.StaleInputs, 'control inputs changed'):
            self.worker.finish(job)
        self.assertEqual(job['epoch'], 0)
        self.assertEqual(after['pose']['foot']['location'][1], .007)
        self.assertFalse(job['handed_off'])
        self.worker.close(job)

    def test_finish_refuses_source_or_candidate_keys_without_any_native_event(self):
        for edited in ('source', 'candidate'):
            with self.subTest(edited=edited):
                job = self.begin(lambda job: iter(()))
                output = Output()
                job.update(state='READY', ready_epoch=job['epoch'], candidate=output,
                    raw_owners=(), fresh_inputs={'raw': {}, 'portable': {'source_metadata': self.worker._digest({})}},
                    result=NS(action=output, slot=12, diagnostics={}), receipt={'action_sha256': 'verified'})
                def current(action, slot):
                    changed = action is self.action if edited == 'source' else action is output
                    return {'settings': 'artist edited keys' if changed else 'unchanged'}
                self.worker._action_capture = Mock(side_effect=current)
                with self.assertRaisesRegex(self.worker.StaleInputs, 'keyframes changed'):
                    self.worker.finish(job)
                self.assertEqual(job['epoch'], 0)
                self.assertFalse(job['handed_off'])
                self.assertFalse(job['closed'])
                self.assertNotIn('mapping', output)
                self.worker.close(job)

    def test_point_bulk_capture_retains_exact_enums_handles_and_original_bytes(self):
        enums = (NS(value=0, identifier='BEZIER'), NS(value=1, identifier='LINEAR'))
        def prop(name, kind, width=0):
            return NS(identifier=name, type=kind, array_length=width, is_array=bool(width),
                      array_dimensions=(width, 0, 0), enum_items=enums, is_enum_flag=False)
        schema = NS(properties=[prop('co', 'FLOAT', 2), prop('handle_left', 'FLOAT', 2),
                                prop('interpolation', 'ENUM'), prop('select_control_point', 'BOOLEAN')])
        class Points(list):
            def foreach_get(self, name, destination):
                flat = []
                for point in self:
                    value = getattr(point, name)
                    if name == 'interpolation':
                        value = {'BEZIER': 0, 'LINEAR': 1}[value]
                    flat.extend(value if isinstance(value, tuple) else (value,))
                for index, value in enumerate(flat):
                    destination[index] = value
        points = Points([NS(bl_rna=schema, co=(1., .125), handle_left=(.5, -.25),
                            interpolation='BEZIER', select_control_point=True)])
        frozen = self.worker._point_capture(points, {'select_control_point'})
        self.assertEqual(list(self.worker._captured_points(frozen)),
                         [{'co': [1., .125], 'handle_left': [.5, -.25], 'interpolation': 'BEZIER'}])
        points[0].co, points[0].interpolation = (1., .25), 'LINEAR'
        self.assertNotEqual(frozen, self.worker._point_capture(points, {'select_control_point'}))
        self.assertEqual(list(self.worker._captured_points(frozen))[0]['co'], [1., .125])

    def test_action_hash_after_yield_uses_the_same_immutable_capture(self):
        capture = {'settings': {'motion_setting': 1}, 'slot_type': 'OBJECT', 'layers': []}
        self.worker._action_capture = Mock(return_value=capture)
        iterator = self.worker._action_hash_steps(self.action, 12)
        self.assertIs(next(iterator), self.worker._TURN)
        # A later native read would now see a different Action. The hash must
        # continue over the exact first capture, whose fence is retained.
        self.worker._action_capture.return_value = dict(capture, settings={'motion_setting': 2})
        actual = finish(iterator)
        expected = self.fingerprint._stream_digest({'schema': self.fingerprint.SCHEMA, 'action': capture})
        self.assertEqual(actual, expected)
        self.worker._action_capture.assert_called_once_with(self.action, 12)

    def test_column_point_encoding_matches_canonical_rows_at_chunk_boundaries(self):
        from array import array
        w = self.worker
        def field(name, kind, width, is_array, code, numbers, enums=()):
            return (name, kind, width, is_array, False, enums, code, array(code, numbers).tobytes())
        fields = [
            field('co', 'FLOAT', 2, True, 'f', [-0., .75, 3., .125] * 65),
            field('type', 'ENUM', 1, False, 'i', [0, 1] * 65, ((0, 'KEYFRAME'), (1, 'BREAKDOWN'))),
            field('fixed', 'FLOAT', 1, False, 'f', [2.] * 130),
            field('custom', 'BOOLEAN', 1, False, 'i', [1, 0] * 65),
            field('counts', 'INT', 1, False, 'i', list(range(130))),
        ]
        data = (130, fields)
        expected = self.fingerprint._stream_digest({'keyframe_points': list(w._captured_points(data))})
        self.assertEqual(finish(w._action_stream_steps({'keyframe_points': w._PointRows(data)})), expected)
        fields.append(field('bad', 'FLOAT', 1, False, 'f', [float('inf')] * 130))
        with self.assertRaises(self.fingerprint.FingerprintUnknown):
            finish(w._action_stream_steps({'keyframe_points': w._PointRows(data)}))

    def test_native_source_proof_is_key_order_independent_and_covers_exact_columns(self):
        w=self.worker
        source=(self.action.as_pointer(),12)
        def proof(capture):
            w._action_capture = Mock(return_value=capture)
            return finish(w._action_hash_steps(self.action,12,dict(values={},timings=[],source_columns=source)))
        original={'settings':{'first':1,'second':2},'layers':[{'points':b'original'}]}
        reordered={'layers':[{'points':b'original'}],'settings':{'second':2,'first':1}}
        self.assertEqual(proof(original),proof(reordered))
        self.assertNotEqual(proof(original),proof(dict(original,layers=[{'points':b'edited'}])))

    def test_handoff_cleanup_failure_retains_candidate_owner(self):
        job = self.begin(lambda job: iter(()))
        output = Output()
        job.update(state='READY', ready_epoch=job['epoch'], candidate=output,
                   raw_owners=(), fresh_inputs={'raw': {}, 'portable': {'source_metadata': self.worker._digest({})}}, append_ids=set(),
                   result=NS(action=output, slot=12, diagnostics={}), receipt={'action_sha256': 'verified'})
        self.worker._remove_append_libraries = Mock(side_effect=RuntimeError('unexpected retained user'))
        with self.assertRaisesRegex(RuntimeError, 'cleanup needs recovery'):
            self.worker.finish(job)
        self.assertIs(job['candidate'], output)
        self.assertFalse(job['handed_off'])
        self.assertIs(self.worker._JOBS[str(job['root'])], job)
        self.bpy.data.actions.remove.assert_not_called()
        self.assertFalse(any(call.args[0].name == 'accepted.json' for call in self.worker._write.call_args_list))
        self.worker._remove_append_libraries = Mock()
        self.worker.close(job)

    def test_native_load_freed_candidate_is_not_recovered_by_name(self):
        job = self.begin(lambda job: iter(()))
        output = Output()
        output.as_pointer = Mock(side_effect=ReferenceError('StructRNA removed'))
        job['candidate'] = output
        self.worker.request_cancel(job)
        self.worker.close(job)
        self.bpy.data.actions.remove.assert_not_called()
        self.assertTrue(job['closed'])

    def test_cancel_verified_unbound_candidate_cleans_only_owned_output(self):
        job = self.begin(lambda job: iter(()))
        output = Output()
        job.update(state='READY', candidate=output)
        self.worker.request_cancel(job)
        self.assertEqual(self.worker.poll(job)['state'], 'CANCELLED')
        self.bpy.data.actions.remove.assert_called_once_with(output)
        self.assertFalse(job['handed_off'])

    def test_finish_refuses_edits_after_last_verified_slice(self):
        job = self.begin(lambda job: iter(()))
        output = Output()
        job.update(state='READY', ready_epoch=0, epoch=1, candidate=output,
            raw_owners=(), fresh_inputs={'raw': {}, 'portable': {'source_metadata': self.worker._digest({})}},
            result=NS(action=output, slot=12, diagnostics={}), receipt={'action_sha256': 'verified'})
        self.worker._publication_fresh = Mock(side_effect=self.worker.StaleInputs('Inputs changed after result verification.'))
        with self.assertRaises(self.worker.StaleInputs):
            self.worker.finish(job)
        self.worker._publication_fresh.assert_called_once_with(job)
        self.assertFalse(job['handed_off'])
        self.worker.close(job)

    def test_late_native_notification_requires_full_exact_proof_before_handoff(self):
        job = self.begin(lambda job: iter(()))
        output = Output()
        job.update(state='READY', ready_epoch=0, epoch=1, candidate=output,
            raw_owners=(), fresh_inputs={'raw': {}, 'portable': {'source_metadata': self.worker._digest({})}},
            result=NS(action=output, slot=12, diagnostics={}), receipt={'action_sha256': 'verified'})
        self.worker._publication_fresh = Mock()
        result = self.worker.finish(job)
        self.assertIs(result.action, output)
        self.worker._publication_fresh.assert_called_once_with(job)
        self.assertEqual(job['metrics']['late_notification_rechecks'], 1)
        self.assertTrue(job['handed_off'])

    def test_late_notification_fence_matches_every_original_freshness_field(self):
        isolation = ModuleType(self.mapping.__name__.rsplit('.', 1)[0] + '.animation_controller_isolation')
        isolation._graph = lambda rig: None
        isolation._secondary_sources = lambda rig: (None, ())
        self.enterContext(patch.dict(sys.modules, {isolation.__name__: isolation}))
        self.scene = ID('home')
        self.scene.objects = {'target': self.target}
        self.context.scene = self.scene
        state = dict(source_rest='source Rest', target_rest='target Rest', controls='full controls',
                     raw='raw values', frame=1.)
        properties = {}
        self.action.items = properties.items
        self.worker._scene_inputs = lambda scene: {'frame': state['frame'], 'fps': 30}
        self.mapping._rest_fingerprint = lambda rig: state['source_rest' if rig is self.source else 'target_rest']
        self.worker._control_fingerprint = lambda *args: state['controls']
        self.worker._raw_packet = lambda rig: {'values': state['raw']}
        self.worker._action_hash = Mock(return_value='already proven Action')
        expected = self.worker._fresh_inputs(self.context, self.source, self.action, 12, self.target)
        job = self.begin(lambda job: iter(()))
        job['fresh_inputs'] = expected
        self.worker._action_hash = Mock(side_effect=AssertionError('Do not repeat the slow per-key hash.'))
        self.worker._publication_fresh(job)
        for field in tuple(state):
            with self.subTest(field=field):
                original = state[field]
                state[field] = 2. if field == 'frame' else 'artist edited ' + field
                with self.assertRaises(self.worker.StaleInputs):
                    self.worker._publication_fresh(job)
                state[field] = original
        properties['authored'] = 1
        with self.assertRaises(self.worker.StaleInputs):
            self.worker._publication_fresh(job)
        properties.clear()
        old_data = self.target.data
        self.target.data = ID('replacement data')
        with self.assertRaises(self.worker.StaleInputs):
            self.worker._publication_fresh(job)
        self.target.data = old_data
        job['slot'] = 13
        with self.assertRaises(self.worker.StaleInputs):
            self.worker._publication_fresh(job)
        job['slot'] = 12
        self.worker._publication_fresh(job)
        self.worker._action_hash.assert_not_called()
        self.worker.close(job)

    def test_close_retains_external_candidate_user_for_explicit_recovery(self):
        job = self.begin(lambda job: iter(()))
        output = Output()
        output.users = 1
        job['candidate'] = output
        with self.assertRaisesRegex(RuntimeError, 'cleanup needs recovery'):
            self.worker.close(job)
        self.assertIs(self.worker._JOBS[str(job['root'])], job)
        self.bpy.data.actions.remove.assert_not_called()
        output.users = 0
        self.worker.close(job)


if __name__ == '__main__':
    unittest.main()
