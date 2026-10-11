"""Pure timer/admission tests. No bpy, window, screenshot or native run occurs."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = load('composition_ui_checkpoints')
session = load('composition_session_acceptance')


def options():
    return dict(schema=api.SCHEMA, enabled=True,
        helper={'path':str(TOOLS/'composition_ui_checkpoints.py'),'sha256':'1'*64},
        settleSeconds=2., timeoutSeconds=30.)


class CheckpointTests(unittest.TestCase):
    def startup_failure(self, folder, stage, cleanup_failure=False):
        root = Path(folder)
        calls = {'registered': [], 'unregistered': [], 'timer_unregistered': [], 'closed': [], 'guard': []}
        timer_state = []
        primary = RuntimeError('actual ' + stage + ' startup failure')
        def register_class(cls):
            if stage == 'class' and len(calls['registered']) == 2:
                raise primary
            calls['registered'].append(cls)
        def unregister_class(cls):
            calls['unregistered'].append(cls)
            if cleanup_failure and len(calls['unregistered']) == 1:
                raise RuntimeError('one class cleanup failed')
        def register_timer(timer, **kwargs):
            if stage == 'partial_timer':
                timer_state.append(timer)
            raise primary
        def unregister_timer(timer):
            calls['timer_unregistered'].append(timer)
            timer_state.remove(timer)
        bpy = NS(app=NS(background=False, version_string='pure-test',
                timers=NS(register=register_timer,is_registered=lambda timer:timer in timer_state,
                          unregister=unregister_timer)),
            context=NS(window=NS()), types=NS(Operator=object,Panel=object),
            utils=NS(user_resource=lambda *_args,**_kwargs:str(root/'profile'),
                     register_class=register_class,unregister_class=unregister_class))
        iterator = NS(close=lambda:calls['closed'].append(True))
        original = api.Controller.persist
        persistence_calls = []
        def persist(controller):
            persistence_calls.append(controller)
            if stage == 'persist' and len(persistence_calls) == 1:
                raise primary
            return original(controller)
        api._CONTROLLER = None
        with patch.dict('sys.modules',{'bpy':bpy}), patch.dict(api.os.environ,
                {'BLENDER_USER_CONFIG':str(root/'profile'),'BLENDER_USER_DATAFILES':str(root/'datafiles')}), \
                patch.object(api.Controller,'persist',persist):
            with self.assertRaises(RuntimeError) as caught:
                api.start(bpy.context,iterator,options(),root,finished=lambda *_args:self.fail('startup must not finish'),
                    pin_guard=lambda:calls['guard'].append(True),state_reader=lambda entry:entry)
        self.assertIs(caught.exception,primary)
        self.assertIsNone(api._CONTROLLER)
        self.assertEqual(calls['unregistered'],list(reversed(calls['registered'])))
        self.assertEqual(calls['closed'],[True]); self.assertEqual(calls['guard'],[True])
        self.assertEqual(timer_state,[])
        self.assertEqual(len(calls['timer_unregistered']),int(stage == 'partial_timer'))
        report = json.loads((root/'headful_checkpoint_report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['startup_failed']); self.assertFalse(report['completed'])
        self.assertTrue(report['native_pending']); self.assertTrue(report['protected_inputs_verified_on_stop'])
        self.assertEqual(report['error'],str(primary))
        return report

    def test_class_startup_failure_removes_only_successfully_registered_classes(self):
        with tempfile.TemporaryDirectory() as folder:
            self.startup_failure(folder,'class')

    def test_initial_report_failure_rolls_back_registration_and_controller(self):
        with tempfile.TemporaryDirectory() as folder:
            self.startup_failure(folder,'persist')

    def test_timer_startup_failure_and_partial_registration_both_roll_back(self):
        for stage in ('timer','partial_timer'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as folder:
                self.startup_failure(folder,stage)

    def test_failed_class_cleanup_still_attempts_every_class_and_preserves_primary_error(self):
        with tempfile.TemporaryDirectory() as folder:
            report = self.startup_failure(folder,'timer',cleanup_failure=True)
            self.assertEqual(report['class_cleanup_errors'],['one class cleanup failed'])

    def test_explicit_completion_quit_waits_for_grace_and_rechecks_guard(self):
        calls, timers = [], []
        bpy = NS(app=NS(background=False,timers=NS(register=lambda timer,**kwargs:timers.append((timer,kwargs)))),
            context=NS(window=NS()),ops=NS(wm=NS(quit_blender=lambda:calls.append('quit') or {'FINISHED'})))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'run.json'; report = {'completed':True,'headful_pending':False}
            api.schedule_completion_quit(bpy,report,path,lambda:calls.append('guard'))
            self.assertEqual(calls,[])
            self.assertEqual(timers[0][1],{'first_interval':3.,'persistent':False})
            self.assertEqual(json.loads(path.read_text())['quit_status'],'scheduled')
            self.assertIsNone(timers[0][0]())
            self.assertEqual(calls,['guard','quit'])
            self.assertEqual(json.loads(path.read_text())['quit_status'],'normal_quit_requested')

    def test_completion_guard_failure_preserves_open_window_and_failed_report(self):
        calls, timers = [], []
        bpy = NS(app=NS(background=False,timers=NS(register=lambda timer,**kwargs:timers.append(timer))),
            context=NS(window=NS()),ops=NS(wm=NS(quit_blender=lambda:calls.append('quit') or {'FINISHED'})))
        def changed():
            raise RuntimeError('immutable Source changed')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'run.json'; report = {'completed':True,'headful_pending':False}
            api.schedule_completion_quit(bpy,report,path,changed)
            timers[0]()
            self.assertEqual(calls,[]); self.assertFalse(report['completed'])
            self.assertEqual(json.loads(path.read_text())['quit_error'],'immutable Source changed')
            with self.assertRaises(AssertionError): api.schedule_completion_quit(bpy,report,path,changed)

    def test_partial_quit_timer_failure_removes_only_own_timer_without_quitting(self):
        timers, removed, calls = [], [], []
        def failed_register(timer,**kwargs):
            timers.append(timer)
            raise RuntimeError('actual quit timer schedule failure')
        def remove(timer):
            removed.append(timer); timers.remove(timer)
        bpy = NS(app=NS(background=False,timers=NS(register=failed_register,
                is_registered=lambda timer:timer in timers,unregister=remove)),
            context=NS(window=NS()),ops=NS(wm=NS(quit_blender=lambda:calls.append('quit'))))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'run.json'; report = {'completed':True,'headful_pending':False}
            api.schedule_completion_quit(bpy,report,path,lambda:calls.append('guard'))
            self.assertEqual(calls,[]); self.assertEqual(timers,[]); self.assertEqual(len(removed),1)
            self.assertFalse(report['completed'])
            self.assertEqual(json.loads(path.read_text())['quit_error'],'actual quit timer schedule failure')

    def test_import_and_validation_need_no_blender(self):
        value = options()
        self.assertIs(api.validate_options(value),value)
        self.assertIsNone(api._CONTROLLER)

    def test_unknown_options_nonfinite_wait_and_unpinned_helper_reject(self):
        for key, value in [('enabled',False),('settleSeconds',True),('timeoutSeconds',float('inf')),
                           ('helper',{'path':'relative.py','sha256':'1'*64})]:
            changed = options(); changed[key] = value
            with self.assertRaises(AssertionError): api.validate_options(changed)
        changed = options(); changed['sync'] = True
        with self.assertRaises(AssertionError): api.validate_options(changed)

    def test_profile_requires_separate_owned_config_and_datafiles(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            self.assertEqual(api.owned_profile(base,base/'profile',base/'datafiles'),
                             [base/'profile',base/'datafiles'])
            for config, data in [('',base/'datafiles'),(base,base/'datafiles'),
                                 (base/'datafiles',base/'datafiles'),(base.parent,base/'datafiles')]:
                with self.assertRaises(AssertionError): api.owned_profile(base,config,data)

    def test_generated_capture_paths_never_overwrite_or_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            path = api.capture_path(folder,1,'c_edit_b_weapon')
            path.write_bytes(b'prior evidence; no image generated')
            with self.assertRaises(AssertionError): api.capture_path(folder,1,'c_edit_b_weapon')
            for name in ('../foreign','C fake','x'*49):
                with self.assertRaises(AssertionError): api.capture_path(folder,1,name)

    def test_default_run_drains_generator_and_preserves_result(self):
        def steps(*_args):
            yield {'name':'first'}
            yield {'name':'second'}
            return {'complete':True,'actual_result':'preserved'}
        with patch.object(session,'run_steps',steps):
            self.assertEqual(session.run(None,'plain-id',{}),{'complete':True,'actual_result':'preserved'})

    def controller(self, folder, iterator, guard=lambda:None):
        bpy = NS(app=NS(version_string='pure-test'),
            context=NS(scene=NS(frame_current_final=1.),window=NS(screen=NS(is_animation_playing=False))),
            utils=NS(unregister_class=lambda _cls:None))
        finished = []
        controller = api.Controller(bpy,iterator,options(),folder,
            lambda result, report, error:finished.append((result,deepcopy(report),error)),guard)
        controller.prepare_view = lambda:None
        controller.verify_captures = lambda:None  # scheduling tests generate no pixels
        captures = []
        def capture():
            captures.append(controller.current['name'])
            # Test scheduling only; no pixels/files are fabricated.
            controller.current['captures'].append({'scheduling_test':True})
        controller.capture = capture
        return controller, finished, captures

    def test_timer_settles_without_advancing_until_real_capture_ack(self):
        def steps():
            yield {'name':'first','time_mode':'HoldPose'}
            yield {'name':'second','time_mode':'HoldPose'}
            return {'complete':True}
        with tempfile.TemporaryDirectory() as folder, patch.object(api.time,'monotonic',return_value=10.) as clock:
            controller, finished, captures = self.controller(folder,steps())
            self.assertEqual(controller.tick(),.1)
            self.assertEqual(controller.current['name'],'first')
            with self.assertRaises(AssertionError): controller.request_continue()
            clock.return_value = 11.; controller.tick(); self.assertEqual(captures,[])
            clock.return_value = 12.; controller.tick(); self.assertEqual(captures,['first'])
            controller.tick(); self.assertEqual(controller.current['name'],'first')
            controller.request_continue(); controller.tick()
            self.assertEqual(controller.current['name'],'second')
            clock.return_value = 14.; controller.tick(); controller.request_continue()
            self.assertIsNone(controller.tick())
            self.assertEqual(finished[0][0],{'complete':True})
            self.assertTrue(finished[0][1]['raise_visual_pending'])

    def test_timeout_closes_generator_and_runs_native_guard_again(self):
        finalized, guards = [], []
        def steps():
            try:
                yield {'name':'first'}
            finally:
                finalized.append(True)
        with tempfile.TemporaryDirectory() as folder, patch.object(api.time,'monotonic',return_value=0.) as clock:
            controller, finished, _captures = self.controller(folder,steps(),lambda:guards.append(True))
            controller.tick(); clock.return_value = 31.
            self.assertIsNone(controller.tick())
            self.assertEqual(finalized,[True])
            self.assertGreaterEqual(len(guards),2)
            self.assertIsInstance(finished[0][2],TimeoutError)
            self.assertTrue(finished[0][1]['protected_inputs_verified_on_stop'])

    def test_cancel_guard_failure_prevents_success_and_still_finalizes(self):
        finalized = []
        def steps():
            try:
                yield {'name':'first'}
            finally:
                finalized.append(True)
        with tempfile.TemporaryDirectory() as folder, patch.object(api.time,'monotonic',return_value=0.):
            controller, finished, _captures = self.controller(folder,steps())
            controller.tick()
            def changed():
                raise AssertionError('source changed')
            controller.pin_guard = changed
            controller.abort_error = RuntimeError('explicit Cancel')
            controller.tick()
            self.assertEqual(finalized,[True])
            self.assertFalse(finished[0][1]['completed'])
            self.assertFalse(finished[0][1]['protected_inputs_verified_on_stop'])

    def test_only_actual_acknowledged_raise_changes_raise_pending_status(self):
        def steps():
            yield {'name':'actual_raise','time_mode':'Raise','following_hold_work_item_id':'actual-work-id'}
            return {'complete':True}
        with tempfile.TemporaryDirectory() as folder, patch.object(api.time,'monotonic',return_value=0.) as clock:
            controller, finished, _captures = self.controller(folder,steps())
            controller.tick()
            self.assertTrue(controller.report['raise_visual_pending'])
            clock.return_value = 2.; controller.tick(); controller.request_continue(); controller.tick()
            self.assertFalse(finished[0][1]['raise_visual_pending'])

    def test_fresh_state_rejects_changed_actual_edit_target_before_capture(self):
        def steps():
            yield {'name':'first','editing':'B'}
        with tempfile.TemporaryDirectory() as folder, patch.object(api.time,'monotonic',return_value=0.):
            controller, _finished, _captures = self.controller(folder,steps())
            controller.tick()
            controller.state_reader = lambda checkpoint:{**checkpoint,'editing':'A'}
            with self.assertRaisesRegex(AssertionError,'editing'): controller.fresh_state()

    def test_paused_checkpoint_rejects_playback_or_changed_frame(self):
        def steps():
            yield {'name':'paused_seek','playing':False,'frame':9.22}
        with tempfile.TemporaryDirectory() as folder, patch.object(api.time,'monotonic',return_value=0.):
            controller, _finished, _captures = self.controller(folder,steps())
            controller.tick()
            for key, value, message in [('playing',True,'playing'),('frame',10.,'paused checkpoint frame')]:
                controller.state_reader = lambda checkpoint,key=key,value=value:{**checkpoint,key:value}
                with self.assertRaisesRegex(AssertionError,message): controller.fresh_state()

    def test_playing_checkpoint_allows_only_frame_progression(self):
        def steps():
            yield {'name':'actual_play','playing':True,'frame':1.}
        with tempfile.TemporaryDirectory() as folder, patch.object(api.time,'monotonic',return_value=0.):
            controller, _finished, _captures = self.controller(folder,steps())
            controller.tick()
            controller.state_reader = lambda checkpoint:{**checkpoint,'frame':12.}
            self.assertEqual(controller.fresh_state()['frame'],12.)
            controller.state_reader = lambda checkpoint:{**checkpoint,'playing':False}
            with self.assertRaisesRegex(AssertionError,'playing'): controller.fresh_state()

    def test_synchronous_entry_refuses_async_options_before_native_work(self):
        with patch.object(session,'run_steps',side_effect=AssertionError('must not start')):
            with self.assertRaisesRegex(AssertionError,'standalone timer'):
                session.run(None,'plain-id',{'headfulCheckpoints':options()})


if __name__ == '__main__':
    unittest.main()
