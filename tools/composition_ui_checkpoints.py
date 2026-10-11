"""Opt-in real-screen checkpoints for one root-owned headful QA process.

Importing this tool does not import bpy, launch a process or capture pixels.
The standalone fixture admits the immutable seed before calling start().
Timers yield Blender's UI thread between checkpoints; no blocking sleep is used.
"""
import hashlib
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import time
import traceback

SCHEMA = 'x.validation.character-composition-ui-checkpoints/1'
FIELDS = {'schema', 'enabled', 'helper', 'settleSeconds', 'timeoutSeconds'}
REOPEN_SCHEMA = 'x.validation.character-composition-ui-reopen-boundary/1'
REOPEN_FIELDS = {'schema', 'phase', 'path', 'sha256', 'sceneName'}
REOPEN_PATHS = {'active_save_reopen': 'composition_active_session.blend',
                'commit_close_and_closed_reopen': 'composition_closed_session.blend'}
_CONTROLLER = None


def require(value, message):
    if not value:
        raise AssertionError(message)


def validate_options(options):
    require(type(options) is dict and set(options) == FIELDS and options['schema'] == SCHEMA
            and options['enabled'] is True, 'Headful checkpoints need the exact explicit opt-in schema.')
    helper = options['helper']
    require(type(helper) is dict and set(helper) == {'path', 'sha256'}
            and type(helper['path']) is str and Path(helper['path']).is_absolute()
            and type(helper['sha256']) is str and re.fullmatch('[0-9a-f]{64}', helper['sha256']),
            'Pin the actual headful checkpoint helper path and SHA-256.')
    for key, low, high in (('settleSeconds', 1., 10.), ('timeoutSeconds', 30., 3600.)):
        value = options[key]
        require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
                'Use finite bounded redraw settling and manual checkpoint deadlines.')
    return options


def capture_path(directory, index, name, attempt=1):
    require(type(index) is int and 1 <= index <= 99 and type(attempt) is int and 1 <= attempt <= 99
            and type(name) is str and re.fullmatch('[a-z][a-z0-9_]{0,47}', name),
            'Use a bounded internal checkpoint label, never a supplied output filename.')
    directory = Path(directory).resolve()
    path = directory / ('ui_%02d_%s_%02d.png' % (index, name, attempt))
    require(path.is_relative_to(directory)
            and len(str(path).encode('utf-16-le')) // 2 < 240,
            'Keep real screenshot files inside the owned directory with Windows path slack.')
    require(not path.exists(), 'A native screenshot must never overwrite prior evidence.')
    return path


def owned_profile(evidence, config_path, datafiles_path):
    evidence = Path(evidence).resolve()
    paths = [Path(value).resolve() for value in (config_path, datafiles_path)]
    require(all(str(value).strip() for value in (config_path, datafiles_path))
            and all(Path(value).is_absolute() for value in (config_path, datafiles_path))
            and all(path != evidence and path.is_relative_to(evidence) for path in paths)
            and paths[0] != paths[1]
            and not paths[0].is_relative_to(paths[1]) and not paths[1].is_relative_to(paths[0]),
            'Headful QA requires distinct explicit BLENDER_USER_CONFIG and DATAFILES children of its owned evidence directory.')
    return paths


@contextmanager
def owned_scene_context(scene_name):
    """Reacquire one owned QA window; never span a file load or generator pause."""
    import bpy
    require(type(scene_name) is str and scene_name.strip()
            and type(bpy.app.background) is bool and not bpy.app.background,
            'The QA Scene context belongs only to its explicit owned headful process.')
    config, datafiles = (os.environ.get(key, '') for key in
                        ('BLENDER_USER_CONFIG', 'BLENDER_USER_DATAFILES'))
    require(config and datafiles and Path(config).is_absolute() and Path(datafiles).is_absolute(),
            'The owned QA process must retain both explicit absolute profile paths.')
    config_path, datafiles_path = Path(config).resolve(), Path(datafiles).resolve()
    require(config_path.parent == datafiles_path.parent
            and config_path.is_dir() and datafiles_path.is_dir(),
            'Use the admitted sibling QA CONFIG and DATAFILES directories.')
    evidence = _CONTROLLER.directory if _CONTROLLER is not None else config_path.parent
    owned_profile(evidence, config, datafiles)
    require(Path(bpy.utils.user_resource('CONFIG', create=False)).resolve() == config_path
            and Path(bpy.utils.user_resource('DATAFILES', create=False)).resolve() == datafiles_path,
            'Both actual Blender profile roots must match the owned process environment.')
    scenes = [scene for scene in bpy.data.scenes if scene.name == scene_name]
    require(len(scenes) == 1 and scenes[0].library is None,
            'The exact local QA Scene must exist uniquely after every load.')
    window_manager = bpy.context.window_manager
    windows = tuple(window_manager.windows) if window_manager is not None else ()
    require(len(windows) == 1, 'The isolated QA process must own exactly one actual Blender window.')
    scene, window = scenes[0], windows[0]
    if window.scene != scene:
        window.scene = scene
    # Keep this QA Scene on the owned window after the scope ends. Restoring an
    # old Scene here would invalidate the next timer/operator's native context.
    with bpy.context.temp_override(window=window):
        context = bpy.context
        require(context.window == window and context.scene == scene and window.scene == scene
                and context.view_layer is not None and context.view_layer == window.view_layer
                and any(layer == context.view_layer for layer in scene.view_layers),
                'The fresh override must expose the exact native QA Scene and its View Layer.')
        context.view_layer.update()
        require(context.window == window and context.scene == scene
                and context.view_layer == window.view_layer,
                'The QA window/Scene/View Layer changed during its native update.')
        yield context


def validate_reopen_boundary(boundary, evidence, scene_name):
    """Admit only the two just-saved owned session files; no Blender access."""
    require(type(boundary) is dict and set(boundary) == REOPEN_FIELDS
            and boundary['schema'] == REOPEN_SCHEMA and type(boundary['phase']) is str
            and boundary['phase'] in REOPEN_PATHS and type(boundary['sceneName']) is str
            and boundary['sceneName'] == scene_name and type(boundary['path']) is str
            and Path(boundary['path']).is_absolute() and type(boundary['sha256']) is str
            and re.fullmatch('[0-9a-f]{64}', boundary['sha256']),
            'A reopen boundary must be the exact five-field owned session command.')
    json.dumps(boundary, allow_nan=False)
    directory, path = Path(evidence).resolve(), Path(boundary['path']).resolve()
    require(path == directory / REOPEN_PATHS[boundary['phase']]
            and len(str(path).encode('utf-16-le')) // 2 < 260 and path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == boundary['sha256'],
            'Reopen only the exact saved active/closed QA file with its current full SHA-256.')
    return dict(boundary)


def _persist(path, report):
    temporary = path.with_suffix('.tmp.json')
    temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def schedule_completion_quit(bpy, report, path, pin_guard, *, scene_name=None):
    """Explicit fixture opt-in only; all acceptance and saves precede this call."""
    require(report.get('completed') is True and not report.get('headful_pending'),
            'Normal QA exit requires completed acceptance and a final report.')
    require(not bpy.app.background and callable(pin_guard),
            'Normal QA exit belongs to the admitted owned headful process.')
    path = Path(path)
    scene_name = scene_name or (bpy.context.scene.name if bpy.context.scene is not None else None)
    require(type(scene_name) is str and scene_name, 'Normal QA exit requires its admitted plain Scene name.')

    def quit_owned_qa():
        try:
            with owned_scene_context(scene_name):
                pin_guard()
                require(report.get('completed') is True and not report.get('headful_pending'),
                        'Failed or pending QA acceptance must remain open for review.')
                report.update(quit_status='normal_quit_requested', process_exit_verified=False)
                _persist(path, report)
                result = bpy.ops.wm.quit_blender()
                require(result == {'FINISHED'}, 'The owned QA process rejected normal quit.')
                # FINISHED proves operator return, not OS process termination.
                report['quit_operator_result'] = sorted(result)
                _persist(path, report)
        except Exception as error:
            report.update(completed=False, quit_status='failed', quit_error=str(error),
                          quit_traceback=traceback.format_exc())
            try:
                _persist(path, report)
            except Exception as persistence_error:
                report['quit_report_persistence_error'] = str(persistence_error)
        return None

    try:
        report.update(quit_status='scheduled', quit_grace_seconds=3.)
        _persist(path, report)
        bpy.app.timers.register(quit_owned_qa, first_interval=3., persistent=False)
    except Exception as error:
        # A failed schedule never forces process termination or hides acceptance.
        report.update(completed=False, quit_status='failed', quit_error=str(error),
                      quit_traceback=traceback.format_exc())
        try:
            if bpy.app.timers.is_registered(quit_owned_qa):
                bpy.app.timers.unregister(quit_owned_qa)
        except Exception as cleanup_error:
            report['quit_timer_cleanup_error'] = str(cleanup_error)
        try:
            _persist(path, report)
        except Exception as persistence_error:
            report['quit_report_persistence_error'] = str(persistence_error)


class Controller:
    """Holds only plain reports and the session generator across file loading."""
    def __init__(self, bpy, iterator, options, evidence, finished, pin_guard,
                 state_reader=lambda checkpoint:dict(checkpoint), *, scene_name=None):
        self.bpy, self.iterator, self.options = bpy, iterator, options
        self.finished, self.pin_guard = finished, pin_guard
        self.state_reader = state_reader
        self.directory = Path(evidence).resolve()
        self.scene_name = scene_name or (bpy.context.scene.name if bpy.context.scene is not None else None)
        require(type(self.scene_name) is str and self.scene_name,
                'The controller retains only its admitted plain QA Scene name.')
        self.path = self.directory / 'headful_checkpoint_report.json'
        require(not self.path.exists(), 'Use new checkpoint evidence; never resume an unverified old controller.')
        self.report = {'schema': SCHEMA, 'completed': False, 'pid': os.getpid(),
            'blender': bpy.app.version_string, 'source': 'bpy.ops.screen.screenshot',
            'native_pending': True, 'checkpoints': [],
            'scene_name': self.scene_name, 'reopen_boundaries': [],
            'raise_visual_pending': True, 'raise_reason': 'Actual Raise requires its later genuine prepared/registered row; a Hold screenshot does not prove Raise.'}
        self.classes = ()
        self.current, self.ready_at, self.deadline = None, 0., 0.
        self.advance_requested, self.recapture_requested, self.done = True, False, False
        self.abort_error = None
        self.timer = self.tick

    def persist(self):
        _persist(self.path, self.report)

    def request_continue(self):
        with owned_scene_context(self.scene_name):
            self._continue_owned()

    def _continue_owned(self):
        require(self.current is not None and self.current['captures'],
                'Capture actual native pixels before continuing this checkpoint.')
        self.pin_guard()
        self.verify_captures()
        self.fresh_state()
        self.current['review_acknowledged'] = True
        self.current['acknowledged_frame'] = self.bpy.context.scene.frame_current_final
        if self.current.get('time_mode') == 'Raise':
            require(self.current.get('following_hold_work_item_id'),
                    'An actual Raise checkpoint must name its authenticated following Hold WorkItem.')
            self.report['raise_visual_pending'] = False
            self.report['raise_reason'] = 'Actual registered Raise native screen captured and explicitly acknowledged.'
        self.advance_requested = True
        self.persist()

    def verify_captures(self):
        for entry in self.report['checkpoints']:
            for capture in entry['captures']:
                path = Path(capture['filepath'])
                require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == capture['sha256'],
                        'A produced native screenshot changed or disappeared before acknowledgement/completion.')

    def fresh_state(self):
        state = self.state_reader(self.current)
        require(type(state) is dict, 'Refresh actual native screenshot state from the admitted current row.')
        json.dumps(state, allow_nan=False)
        for key in ('scene', 'rig', 'item_id', 'work_item_id', 'public_work_item_id',
                    'public_new_action_id', 'editing', 'time_mode', 'solo', 'native_tweak',
                    'readonly_A_sha256', 'scope_proof_sha256', 'draft_sha256', 'B_custom_sha256', 'playing'):
            require(state.get(key) == self.current.get(key),
                    'The checkpoint native identity/editing/Scope changed during review: ' + key)
        if not self.current.get('playing'):
            require(state.get('frame') == self.current.get('frame'),
                    'The paused checkpoint frame changed during review; capture the actual generated seek before continuing.')
        return state

    def prepare_view(self):
        bpy, context = self.bpy, self.bpy.context
        require(not bpy.app.background and context.window is not None,
                'Native screenshots require the actual owned headful Blender window.')
        area = next((area for area in context.window.screen.areas if area.type == 'VIEW_3D'), None)
        require(area is not None, 'Keep a real Viewport in the QA screen for native screenshot review.')
        require(any(candidate.type == 'DOPESHEET_EDITOR' and candidate.ui_type == 'TIMELINE'
                    for candidate in context.window.screen.areas),
                'Keep the actual native Timeline visible alongside the QA Viewport and production panels.')
        area.spaces.active.show_region_ui = True
        for candidate in context.window.screen.areas:
            candidate.tag_redraw()
        return area

    def capture(self):
        with owned_scene_context(self.scene_name):
            self._capture_owned()

    def _capture_owned(self):
        context, area = self.bpy.context, self.prepare_view()
        entry = self.current
        self.pin_guard()
        actual_state = self.fresh_state()
        path = capture_path(self.directory, len(self.report['checkpoints']), entry['name'], len(entry['captures']) + 1)
        region = next(region for region in area.regions if region.type == 'WINDOW')
        with context.temp_override(window=context.window, area=area, region=region):
            result = self.bpy.ops.screen.screenshot(filepath=str(path), check_existing=False)
        require(result == {'FINISHED'} and path.is_file() and path.stat().st_size > 64,
                'Blender did not produce an actual headful screen image.')
        with path.open('rb') as stream:
            require(stream.read(8) == b'\x89PNG\r\n\x1a\n', 'The native screenshot is not a PNG image.')
        entry['captures'].append({'filepath': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'native_state': actual_state,
            'frame': context.scene.frame_current_final,
            'playing': bool(context.window.screen.is_animation_playing),
            'capture': 'Actual Blender SDK screen screenshot; visual acceptance requires review.'})
        self.persist()

    def tick(self):
        if self.done:
            return None
        try:
            with owned_scene_context(self.scene_name):
                response = self._tick_owned()
            if type(response) is dict and response.get('schema') == REOPEN_SCHEMA:
                # All old overrides have ended. Do not inspect loaded RNA in
                # this tick; the persistent next tick enters a fresh QA scope.
                self._reopen(response)
                return .1
            return response
        except Exception as error:
            self.report['error'], self.report['traceback'] = str(error), traceback.format_exc()
            self.stop(None, error)
            return None

    def _tick_owned(self):
        if self.abort_error is not None:
            self.stop(None, self.abort_error)
            return None
        now = time.monotonic()
        if self.advance_requested:
            self.advance_requested = False
            self.pin_guard()
            if self.report['reopen_boundaries'] and self.report['reopen_boundaries'][-1]['native_context_pending']:
                self.report['reopen_boundaries'][-1].update(native_context_pending=False,
                                                           native_context_verified=True)
                self.persist()
            try:
                checkpoint = next(self.iterator)
            except StopIteration as result:
                self.stop(result.value, None)
                return None
            if type(checkpoint) is dict and checkpoint.get('schema') == REOPEN_SCHEMA:
                boundary = validate_reopen_boundary(checkpoint, self.directory, self.scene_name)
                self.current = None
                self.advance_requested = True
                return boundary
            require(type(checkpoint) is dict and type(checkpoint.get('name')) is str,
                    'The actual session helper must yield a plain authenticated checkpoint.')
            json.dumps(checkpoint, allow_nan=False)
            # Validate the generated filename before exposing the checkpoint.
            capture_path(self.directory, len(self.report['checkpoints']) + 1, checkpoint['name'])
            self.current = {**checkpoint, 'captures': [], 'review_acknowledged': False}
            self.report['checkpoints'].append(self.current)
            now = time.monotonic()
            self.ready_at, self.deadline = now + self.options['settleSeconds'], now + self.options['timeoutSeconds']
            self.prepare_view()
            self.persist()
            return .1
        if now > self.deadline:
            raise TimeoutError('The owned QA checkpoint expired before explicit visual acknowledgement.')
        if now >= self.ready_at and (not self.current['captures'] or self.recapture_requested):
            self.recapture_requested = False
            self.capture()
        return .1

    def _reopen(self, boundary):
        boundary = validate_reopen_boundary(boundary, self.directory, self.scene_name)
        entry = {**boundary, 'operator_result': None, 'native_context_pending': True}
        self.report['reopen_boundaries'].append(entry)
        self.persist()
        result = self.bpy.ops.wm.open_mainfile(filepath=boundary['path'], use_scripts=False, load_ui=False)
        entry['operator_result'] = sorted(result)
        require(result == {'FINISHED'}, 'The exact owned active/closed session file did not reopen.')
        # Only plain result/report data is touched after open_mainfile. No
        # Window/Scene/ViewLayer/Rig getter is read until the next timer tick.
        self.persist()

    def stop(self, session, error):
        self.done = True
        try:
            with owned_scene_context(self.scene_name):
                self._stop_owned(session, error)
        except Exception as context_error:
            self.report.update(completed=False, native_pending=True,
                error=str(error or context_error), owned_context_error=str(context_error),
                owned_context_traceback=traceback.format_exc(), native_cleanup_deferred=True)
            # Never close a native generator or invoke its finished callback
            # in an unadmitted/absent QA context. Preserve the failed evidence.
            self.persist()

    def _stop_owned(self, session, error):
        self.done = True
        try:
            self.pin_guard()
            self.verify_captures()
            self.report['protected_inputs_verified_on_stop'] = True
        except Exception as protection_error:
            self.report['protected_inputs_verified_on_stop'] = False
            self.report['protection_error'] = str(protection_error)
            error = error or protection_error
        if error is not None:
            try:
                self.iterator.close()  # restore tool preferences; no implicit commit/save/publication
            except Exception as close_error:
                self.report['generator_close_error'] = str(close_error)
        # Do not leave playback running after a failed visual checkpoint.
        context = self.bpy.context
        if context.window and context.window.screen.is_animation_playing:
            try:
                self.bpy.ops.screen.animation_cancel(restore_frame=False)
            except Exception as playback_error:
                self.report['playback_stop_error'] = str(playback_error)
                error = error or playback_error
        for cls in reversed(self.classes):
            try:
                self.bpy.utils.unregister_class(cls)
            except Exception as unregister_error:
                self.report.setdefault('class_cleanup_errors', []).append(str(unregister_error))
                error = error or unregister_error
        self.classes = ()
        if error is not None:
            self.report['error'] = str(error)
        self.report['completed'] = error is None
        self.report['native_pending'] = error is not None
        self.persist()
        self.finished(session, self.report, error)


def start(context, iterator, options, evidence, *, finished, pin_guard, state_reader):
    """Schedule only after the caller has completed strict seed admission."""
    global _CONTROLLER
    import bpy
    validate_options(options)
    require(callable(state_reader) and callable(finished) and callable(pin_guard),
            'Headful checkpoints require actual native-state/protection readers and completion callback.')
    require(not bpy.app.background and context.window is not None,
            'Opt-in headful checkpoints cannot execute in Blender background mode.')
    owned_profile(evidence, os.environ.get('BLENDER_USER_CONFIG', ''), os.environ.get('BLENDER_USER_DATAFILES', ''))
    require(Path(bpy.utils.user_resource('CONFIG', create=True)).resolve()
            == Path(os.environ['BLENDER_USER_CONFIG']).resolve(),
            'The actual headful Blender configuration must resolve to the explicitly owned profile.')
    require(_CONTROLLER is None or _CONTROLLER.done, 'Finish the owned QA checkpoint controller first.')
    scene_name = context.scene.name
    with owned_scene_context(scene_name):
        controller = Controller(bpy, iterator, options, evidence, finished, pin_guard, state_reader,
                                scene_name=scene_name)

    class QA_OT_continue(bpy.types.Operator):
        bl_idname = 'cdesigner_qa.checkpoint_continue'
        bl_label = 'Continue QA Checkpoint'
        bl_description = 'Acknowledge the real visible QA screen and continue the isolated test'

        def execute(self, _context):
            controller.request_continue()
            return {'FINISHED'}

    class QA_OT_recapture(bpy.types.Operator):
        bl_idname = 'cdesigner_qa.checkpoint_recapture'
        bl_label = 'Capture Current QA Screen'

        def execute(self, _context):
            controller.recapture_requested = True
            return {'FINISHED'}

    class QA_OT_cancel(bpy.types.Operator):
        bl_idname = 'cdesigner_qa.checkpoint_cancel'
        bl_label = 'Cancel QA Checkpoints'
        bl_description = 'Stop the isolated check and retain its owned draft without commit or publication'

        def execute(self, _context):
            # Unregister temporary classes only after this operator returns.
            controller.abort_error = RuntimeError('The owned QA checkpoints were explicitly cancelled.')
            return {'FINISHED'}

    class QA_PT_checkpoint(bpy.types.Panel):
        bl_label = 'QA Visual Checkpoint'
        bl_idname = 'CDESIGNER_QA_PT_checkpoint'
        bl_space_type, bl_region_type, bl_category = 'VIEW_3D', 'UI', 'Character Designer'
        bl_order = -100

        def draw(self, _context):
            layout, entry = self.layout, controller.current
            if entry is None:
                layout.label(text='Waiting for authenticated native state')
                return
            layout.label(text=entry['name'].replace('_', ' '))
            layout.label(text=entry.get('classification', 'Actual owned QA state'))
            layout.label(text='Readonly A: ' + entry.get('readonly_A_name', ''))
            if entry.get('editing'):
                layout.label(text='Editing ' + entry['editing'])
            layout.label(text='QA PID ' + str(controller.report['pid']))
            row = layout.row()
            row.enabled = bool(entry['captures'])
            row.operator('cdesigner_qa.checkpoint_continue', icon='CHECKMARK')
            layout.operator('cdesigner_qa.checkpoint_recapture', icon='RENDER_STILL')
            layout.operator('cdesigner_qa.checkpoint_cancel', icon='CANCEL')
            if controller.report['raise_visual_pending']:
                layout.label(text='Actual Raise proof pending later prepared row', icon='INFO')

    try:
        with owned_scene_context(scene_name):
            for cls in (QA_OT_continue, QA_OT_recapture, QA_OT_cancel, QA_PT_checkpoint):
                bpy.utils.register_class(cls)
                controller.classes += (cls,)
            _CONTROLLER = controller
            controller.persist()
            # Each persistent tick reacquires context after the deliberate reopen.
            bpy.app.timers.register(controller.timer, first_interval=.1, persistent=True)
    except Exception as error:
        # Startup is one transaction. Keep its primary exception even when
        # cleanup/report persistence also fails; do not invoke finished twice.
        controller.done = True
        controller.report.update(completed=False, native_pending=True, startup_failed=True,
            error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        try:
            if bpy.app.timers.is_registered(controller.timer):
                bpy.app.timers.unregister(controller.timer)
        except Exception as timer_error:
            controller.report['timer_cleanup_error'] = str(timer_error)
        for cls in reversed(controller.classes):
            try:
                bpy.utils.unregister_class(cls)
            except Exception as class_error:
                controller.report.setdefault('class_cleanup_errors', []).append(str(class_error))
        controller.classes = ()
        _CONTROLLER = None
        try:
            with owned_scene_context(scene_name):
                iterator.close()
        except Exception as close_error:
            controller.report['generator_close_error'] = str(close_error)
        try:
            with owned_scene_context(scene_name):
                pin_guard()
            controller.report['protected_inputs_verified_on_stop'] = True
        except Exception as protection_error:
            controller.report.update(protected_inputs_verified_on_stop=False,
                protection_error=str(protection_error))
        try:
            controller.persist()
        except Exception as persistence_error:
            controller.report['failure_report_persistence_error'] = str(persistence_error)
        raise
    return {'pending': True, 'pid': controller.report['pid'], 'report': str(controller.path)}
