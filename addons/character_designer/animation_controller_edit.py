"""Explicit controller preparation and independently retained motion editing."""

import math
import time

import bpy

_OPENING = False
_POLLING = False
_PENDING = {}
_ACTIVE_INTERVAL = .016
_CHILD_INTERVAL = .25


def _target(home, expected):
    target = home.character_designer_setup.rig
    if (home.library or home.override_library or target is None or target != expected
            or target.type != 'ARMATURE' or target.library or target.override_library
            or not target.is_editable or target.data.library or target.data.override_library
            or home.objects.get(target.name) != target or target.mode == 'EDIT'):
        raise ValueError('The controller edit needs its locally editable character in the original scene outside Edit Mode.')
    return target


def _record(home, row, *, create=True):
    records = home.character_designer_controller_motions
    identity = home.character_designer_animation_worklist.workspace_identity
    key = row.work_item_id or row.clip_key
    target = home.character_designer_setup.rig
    matches = [record for record in records if record.workspace_identity == identity
               and record.work_item_id == key and record.target == target]
    if len(matches) > 1:
        raise ValueError('This motion has multiple saved controller edits. Resolve their identity before opening it.')
    if matches:
        return matches[0]
    if not create:
        return None
    index = len(records)
    record = records.add()
    try:
        record.workspace_identity = identity
        record.work_item_id = key
        record.clip_key = row.clip_key
        record.target = target
    except Exception:
        records.remove(index)
        raise
    return record


def _publish(record, row, built):
    values = dict(source_rig=row.rig, source_action=row.custom_action, source_slot=row.custom_slot,
                  source_fingerprint=built.source_fingerprint, controller_action=built.action,
                  controller_slot=built.slot, state='READY', error='')
    previous = {key: getattr(record, key) for key in values}
    try:
        for key, value in values.items():
            setattr(record, key, value)
    except Exception as exc:
        failures = []
        for key, value in previous.items():
            try:
                setattr(record, key, value)
            except Exception as recovery:
                failures.append(str(recovery))
        if failures:
            raise RuntimeError('Controller edit publication needs recovery: ' + '; '.join(failures)) from exc
        raise


def _native(context, scene, row, message='', *, mapping_needed=False):
    from . import animation_worklist as worklist, animation_worklist_edit_scene as edit_scene
    previous_scene = context.scene
    try:
        context.window.scene = scene
        worklist.activate(context, row.item_id, 'CUSTOM')
    except Exception as exc:
        try:
            setup = getattr(previous_scene, 'character_designer_setup', None)
            if setup is not None and setup.rig is not None and edit_scene.editing_scene(previous_scene) == scene:
                return_to_character(context, previous_scene)
            elif context.window.scene != previous_scene:
                context.window.scene = previous_scene
        except Exception as recovery:
            raise RuntimeError('Native motion activation needs context recovery: ' + str(recovery)) from exc
        raise
    result = dict(state='MAPPING_NEEDED' if mapping_needed else 'NATIVE',
                  message=message or 'Original bone editing.')
    saved = worklist.state(context)
    saved.status, saved.has_error = result['message'], mapping_needed
    return result


def _return_identity(home, rig):
    from . import animation_controller_worker as worker, animation_controller_isolation as isolation
    return (home.character_designer_setup.rig, rig.data, worker._binding(rig),
            home.frame_current, home.frame_subframe,
            worker._digest(worker._value(isolation._graph(rig))))


def _restore_changed_inputs(rig, before, after):
    from . import controller_animation as controller
    if before['constraints'] != after['constraints'] or before['pose'].keys() != after['pose'].keys():
        raise ValueError('The character constraints or bone ownership changed during return.')
    def numeric(value):
        return ((type(value) in (bool, int, float) and math.isfinite(value))
                or isinstance(value, (tuple, list)) and all(numeric(item) for item in value))
    changes = []
    blocks = [(rig, before['object'], after['object'])] + [
        (rig.pose.bones[name], values, after['pose'][name]) for name, values in before['pose'].items()]
    for block, saved, current in blocks:
        if (saved['rotation_mode'] != current['rotation_mode']
                or saved['settings'].keys() != current['settings'].keys()
                or saved['properties'].keys() != current['properties'].keys()):
            raise ValueError('The character input structure changed during return.')
        for field in controller._TRANSFORMS:
            if saved[field] != current[field]:
                changes.append((block, field, saved[field], False))
        for group in ('settings', 'properties'):
            for field, value in saved[group].items():
                if value != current[group][field]:
                    if not numeric(value) or not numeric(current[group][field]):
                        raise ValueError('An unsupported character setting changed during return: ' + field)
                    changes.append((block, field, value, group == 'properties'))
    for block, field, value, custom in changes:
        if custom:
            block[field] = value
        else:
            setattr(block, field, value)


def return_to_character(context, home):
    """Preserve inputs held immediately before this switch, never a departure pose."""
    from . import animation_worklist as worklist, animation_link_source as source
    from . import animation_controller_worker as worker
    worklist._idle(context)
    rig = _target(home, home.character_designer_setup.rig)
    caller = source._context_state(context)
    identity = _return_identity(home, rig)
    before = worker._raw_packet(rig)
    try:
        context.window.scene = home
        if _return_identity(home, rig) != identity:
            raise ValueError('The character binding, frame or ownership changed during return.')
        _restore_changed_inputs(rig, before, worker._raw_packet(rig))
        context.view_layer.update()
        if _return_identity(home, rig) != identity or worker._raw_packet(rig) != before:
            raise RuntimeError('The current character inputs did not survive the native return update.')
    except Exception as exc:
        try:
            source._restore_context(context, caller, playing=False)
            if _return_identity(home, rig) == identity:
                _restore_changed_inputs(rig, before, worker._raw_packet(rig))
        except Exception as recovery:
            raise RuntimeError('Returning to the character needs recovery: ' + str(recovery)) from exc
        raise


def _activate(context, home, record):
    from . import natural_bind_mode, unity_animation as ua
    rig = _target(home, record.target)
    recovery = ''
    try:
        with natural_bind_mode.defer_expiry(rig) as transition:
            result = _activate_provisional(context, home, record)
            transition.commit()
    except natural_bind_mode.ExpiryCommitError as exc:
        recovery = str(exc)
    if not recovery:
        try:
            # The committed expiry can remove a neutral solver and restore its
            # held inputs. Evaluate the selected Action after that boundary.
            ua._set_frame(home, home.frame_current + home.frame_subframe)
            context.view_layer.update()
        except Exception as exc:
            recovery = str(exc)
    if recovery:
        message = 'The controller Action is open, but its pose refresh failed. Open this motion again. ' + recovery
        settings = home.character_designer_animation_worklist
        settings.status, settings.has_error = message, True
        return dict(state='CONTROLLER_RECOVERY', message=message)
    return result


def _activate_provisional(context, home, record):
    from .animation_local_motion_pins import assign_character_action
    from .animation_original_inputs_ui import original_inputs_live
    from . import animation_worklist as worklist, animation_composition as composition
    from . import animation_link_source as source, unity_animation as ua
    from . import controller_animation as controller
    if record.state != 'READY':
        raise ValueError('The saved controller edit is incomplete. Its Action was retained for recovery.')
    rig = _target(home, record.target)
    caller = source._context_state(context)
    saved = None
    try:
        worklist._idle(context)
        if context.scene != home:
            return_to_character(context, home)
        else:
            context.window.scene = home
        worklist._idle(context)
        if original_inputs_live(context):
            raise ValueError('Finish editing Original Rigging Inputs first.')
        if composition.active(rig):
            raise ValueError('Close Composition before switching controller Actions; both drafts are retained.')
        if ua.active_preview(rig):
            raise ValueError('Restore the unrelated Unity test preview before switching controller Actions.')
        if context.view_layer.objects.get(rig.name) != rig:
            raise ValueError('The character must be available in the current view layer.')
        start, end = map(float, record.controller_action.frame_range)
        if not math.isfinite(start) or not math.isfinite(end) or start > end:
            raise ValueError('The controller Action has an invalid frame range.')
        ad = rig.animation_data
        prior_action = ad.action if ad else None
        settings = home.character_designer_animation_worklist
        window_settings = context.window_manager.character_designer_animation
        range_fields = ('frame_start', 'frame_end', 'frame_preview_start', 'frame_preview_end', 'use_preview_range')
        saved = dict(context=source._context_state(context), ad=ad,
                     action=prior_action, slot=ad.action_slot if ad else None,
                     fake=prior_action.use_fake_user if prior_action else None,
                     animation={key: getattr(ad, key) for key in ('use_nla', 'action_blend_type', 'action_influence')} if ad else {},
                     range={key: getattr(home, key) for key in range_fields},
                     frame=home.frame_current + home.frame_subframe,
                     auto_key=home.tool_settings.use_keyframe_insert_auto,
                     hidden=rig.hide_get(), target=window_settings.target,
                     pose=controller._raw(rig, tuple(bone.name for bone in rig.pose.bones)),
                     catalog_index=settings.catalog_index,
                     status=settings.status, has_error=settings.has_error)
        ua._set_playing(context, False)
        assign_character_action(rig, record.controller_action, record.controller_slot)
        ad = rig.animation_data
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.0
        home.tool_settings.use_keyframe_insert_auto = False
        home.frame_start = home.frame_preview_start = math.floor(start)
        home.frame_end = home.frame_preview_end = max(math.ceil(end), home.frame_start)
        home.use_preview_range = True
        ua._set_frame(home, start)
        for selected in context.selected_objects:
            selected.select_set(False)
        rig.hide_set(False)
        rig.select_set(True)
        context.view_layer.objects.active = rig
        window_settings.target = rig
        context.view_layer.update()
        selected_index = next((i for i, row in enumerate(settings.catalog) if row.clip_key == record.clip_key), None)
        if selected_index is not None:
            settings.catalog_index = selected_index
        settings.status, settings.has_error = 'Controller editing.', False
    except Exception as exc:
        failures = []
        def restore(label, callback):
            try:
                callback()
            except Exception as recovery:
                failures.append(label + ': ' + str(recovery))
        if saved is not None:
            if saved['ad'] is None:
                restore('animation data', rig.animation_data_clear)
            else:
                restore('Action', lambda: setattr(saved['ad'], 'action', saved['action']))
                restore('Action slot', lambda: setattr(saved['ad'], 'action_slot', saved['slot']))
                for key, value in saved['animation'].items():
                    restore(key, lambda key=key, value=value: setattr(saved['ad'], key, value))
            if saved['action'] is not None:
                restore('Action retention', lambda: setattr(saved['action'], 'use_fake_user', saved['fake']))
            for key, value in saved['range'].items():
                restore(key, lambda key=key, value=value: setattr(home, key, value))
            restore('auto key', lambda: setattr(home.tool_settings, 'use_keyframe_insert_auto', saved['auto_key']))
            restore('frame', lambda: ua._set_frame(home, saved['frame']))
            restore('held character inputs', lambda: controller._restore_raw(rig, saved['pose']))
            restore('character context', lambda: source._restore_context(context, saved['context'], playing=False))
            restore('character pose evaluation', lambda: saved['context']['view_layer'].update())
            restore('character visibility', lambda: rig.hide_set(saved['hidden']))
            restore('animation target', lambda: setattr(window_settings, 'target', saved['target']))
            restore('catalog selection', lambda: setattr(settings, 'catalog_index', saved['catalog_index']))
            restore('status', lambda: setattr(settings, 'status', saved['status']))
            restore('error status', lambda: setattr(settings, 'has_error', saved['has_error']))
        restore('caller context', lambda: source._restore_context(context, caller, playing=True))
        if failures:
            raise RuntimeError('Controller motion opening needs recovery: ' + '; '.join(failures)) from exc
        raise
    return dict(state='CONTROLLER', message='Controller editing.')


def _resolve_row(context, home, clip_key):
    from . import animation_worklist as worklist, animation_worklist_edit_scene as edit_scene
    scene = edit_scene.editing_scene(home)
    if scene is None or context.window is None:
        raise ValueError('The editing scene for this workspace is unavailable.')
    if sum(row.clip_key == clip_key for row in scene.character_designer_animation_worklist.items) > 1:
        raise ValueError('This motion has multiple source work items. Resolve their identity before opening it.')
    row = edit_scene._local_row(scene, clip_key)
    if row is None:
        if not any(entry.clip_key == clip_key and entry.ready
                   for entry in home.character_designer_animation_worklist.catalog):
            raise ValueError('This motion is no longer in the prepared list.')
        previous = context.window.scene
        try:
            context.window.scene = scene
            row = worklist.add(context, clip_key, activate_new=False, prepare_controls=False)
        finally:
            if previous == home:
                return_to_character(context, home)
            elif context.window.scene != previous:
                context.window.scene = previous
    return scene, row


def _open_context(context, rig):
    """A delayed open may only act on the same pose, selection and editing context."""
    from . import animation_link_source, animation_controller_worker, controller_animation
    scene = context.scene
    return (context.window, context.window.workspace, context.window.screen,
            animation_link_source._context_state(context), scene.frame_current, scene.frame_subframe,
            scene.tool_settings.use_keyframe_insert_auto, scene.frame_start, scene.frame_end,
            scene.use_preview_range, scene.frame_preview_start, scene.frame_preview_end,
            rig.data.pose_position, rig.data.bones.active, tuple(context.selected_pose_bones or ()),
            animation_controller_worker._binding(rig), controller_animation._raw(rig, rig.pose.bones.keys()))


def open_motion(context, home, clip_key, *, asynchronous=False, prepare_on_open=False):
    """Open an existing edit or, on a home-scene click, prepare the missing FK edit."""
    global _OPENING
    from . import animation_worklist_edit_scene as edit_scene
    if _OPENING:
        raise RuntimeError('Finish opening the current motion before opening another controller edit.')
    _OPENING = True
    try:
        # Resolve identity without importing source data in this click handler.
        scene = edit_scene.editing_scene(home)
        matches = [row for row in scene.character_designer_animation_worklist.items
                   if row.clip_key == clip_key] if scene is not None else []
        if len(matches) > 1:
            raise ValueError('This motion has multiple source work items. Resolve their identity before opening it.')
        if not matches:
            matches = [row for row in home.character_designer_animation_worklist.catalog
                       if row.clip_key == clip_key and row.ready]
            if len(matches) > 1:
                raise ValueError('This motion has multiple catalog entries. Resolve their identity before opening it.')
        if not matches:
            raise ValueError('This motion is no longer in the prepared list.')
        row = matches[0]
        record = _record(home, row, create=False)
        _, queue = _queue_for_home(home)
        if queue is not None:
            queue.pop('open_request', None)  # A later click supersedes the old intended edit.
        if record is not None and record.controller_action is not None:
            return _activate(context, home, record)
        if (prepare_on_open and context.scene == home and not _authored_row(row)
                and (record is None or record.state != 'MAPPING_NEEDED')):
            rig = _target(home, home.character_designer_setup.rig)
            before = _open_context(context, rig)
            result = _enqueue(context, home, [row], daily_edit=True)
            pending_job = _PENDING[result['job_id']]
            if not pending_job['cancel_requested'] and pending_job['window'] == context.window:
                ad = rig.animation_data
                active_action = ad.action if ad else None
                pending_job['open_request'] = (clip_key, before, active_action)
            return result
        message = 'Click Prepare to use this motion on the character.'
        if record is not None and record.state == 'MAPPING_NEEDED':
            message = 'This motion needs controller mapping. Click Prepare to retry.'
            if record.error:
                message += ' ' + record.error
        settings = home.character_designer_animation_worklist
        settings.status, settings.has_error = message, False
        return dict(state='PREPARE_REQUIRED', message=message)
    finally:
        _OPENING = False


def open_source_motion(context, home, clip_key):
    """Explicit advanced entry to the retained original-bone editing scene."""
    global _OPENING
    from . import animation_worklist as worklist
    if _OPENING:
        raise RuntimeError('Finish opening the current motion before opening another controller edit.')
    _OPENING = True
    try:
        worklist._idle(context)
        scene, row = _resolve_row(context, home, clip_key)
        return _native(context, scene, row)
    finally:
        _OPENING = False


def _eligible_rows(home):
    """All workspace rows, independent of UI selection, search and pin filters."""
    from . import animation_worklist_edit_scene as edit_scene
    scene = edit_scene.editing_scene(home)
    if scene is None:
        return ()
    rows = []
    source_rows = scene.character_designer_animation_worklist.items
    counts = {}
    for row in source_rows:
        counts[row.clip_key] = counts.get(row.clip_key, 0) + 1
    seen = set(counts)
    for row in source_rows:
        if (row.clip_key and not _authored_row(row) and row.rig is not None
                and row.custom_action is not None and row.source_action is not None
                and counts[row.clip_key] == 1):
            rows.append(row)
    for row in home.character_designer_animation_worklist.catalog:
        if row.clip_key and not _authored_row(row) and row.ready and row.clip_key not in seen:
            rows.append(row)
            seen.add(row.clip_key)
    return tuple(rows)


def _authored_row(row):
    return bool(getattr(row, 'new_animation', False) or getattr(row, 'new_action_id', '')
                or getattr(row, 'current_rig_revision', '') or row.clip_key.startswith('new:')
                or getattr(row, 'composition_json', ''))


def missing_motions(home):
    """Known mapping failures require explicit Retry, never an automatic bulk retry."""
    rows = []
    for row in _eligible_rows(home):
        try:
            record = _record(home, row, create=False)
        except ValueError:
            continue  # Ambiguous saved edits need review, never replacement.
        if record is None or (record.controller_action is None and record.state != 'MAPPING_NEEDED'):
            rows.append(row)
    return tuple(rows)


def _missing_full_ik(home):
    rows = []
    for row in _eligible_rows(home):
        try:
            record = _record(home, row, create=False)
            if _needs_full_ik(record):
                rows.append(row)
        except ValueError:
            continue
    return rows


def _queue_for_home(home):
    for key, item in _PENDING.items():
        try:
            if item['home'] == home:
                return key, item
        except ReferenceError:
            continue
    return None, None


def pending(home, clip_key=None):
    key, item = _queue_for_home(home)
    if item is None:
        return None
    queued = clip_key is not None and clip_key != item.get('clip_key')
    if queued and not any(entry['clip_key'] == clip_key for entry in item['remaining']):
        return None
    job = item.get('job')
    phase = 'Cancelling' if item['cancel_requested'] else ('Queued' if queued else (
        job['phase'] if job is not None else item['phase']))
    detail = job.get('progress_detail', {}) if job and job.get('state') == 'RUNNING' and not (
        queued or item['cancel_requested']) else {}
    return dict(job_id=key, phase=phase, elapsed=max(0, int(time.monotonic() - item['started'])),
                progress_detail=detail,
                cancelling=item['cancel_requested'], error=item.get('error', ''),
                completed=item['completed'], skipped=item['skipped'], failed=item['failed'],
                total=item['total'], name=item.get('name', ''), queued=queued)


def _enqueue(context, home, rows, *, retry=False, daily_edit=None, from_fk=False, pole_alignment=''):
    from . import animation_worklist as worklist
    import uuid
    worklist._idle(context)
    if context.window is None:
        raise ValueError('Prepare controls from an active Blender window.')
    target = _target(home, home.character_designer_setup.rig)
    if daily_edit is None:
        daily_edit = not getattr(home.character_designer_animation_worklist, 'precise_imported_motions', False)
    entries = [dict(clip_key=row.clip_key, work_item_id=getattr(row, 'work_item_id', ''), daily_edit=daily_edit, from_fk=from_fk,
                    catalog_only=not hasattr(row, 'custom_action'), retry=retry, name=row.name) for row in rows]
    if from_fk or pole_alignment:
        for row, entry in zip(rows, entries):
            record = _record(home, row, create=False)
            entry['retained_origin'] = (record.controller_action, record.controller_slot)
            entry['pole_alignment'] = pole_alignment
    existing_key, existing = _queue_for_home(home)
    if existing is not None:
        if existing['cancel_requested']:
            return dict(state='PENDING', message='Wait for cancellation to finish before preparing again.', job_id=existing_key)
        if existing['target'] != target or existing['workspace'] != home.character_designer_animation_worklist.workspace_identity:
            raise ValueError('Finish or cancel the previous character preparation first.')
        queued = {entry['clip_key'] for entry in existing['remaining']} | {existing['clip_key']}
        added = [entry for entry in entries if entry['clip_key'] not in queued]
        existing['remaining'].extend(added)
        existing['total'] += len(added)
        _redraw_pending()
        return dict(state='PENDING', message='Controller preparation is queued.', job_id=existing_key)
    if not entries:
        return dict(state='READY', message='No missing controller motions to prepare.')
    key = uuid.uuid4().hex
    layer = context.view_layer if context.scene == home else home.view_layers[0]
    _PENDING[key] = dict(home=home, target=target, layer=layer, window=context.window,
                         workspace=home.character_designer_animation_worklist.workspace_identity,
                         remaining=entries, clip_key=None, name='', job=None,
                         started=time.monotonic(), phase='Queued', cancel_requested=False,
                         completed=0, failed=0, skipped=0, total=len(entries), error='')
    try:
        _start_runtime()
    except BaseException:
        _PENDING.pop(key, None)
        raise
    _redraw_pending()
    return dict(state='PENDING', message='Controller preparation queued. Completed motions stay ready to open.', job_id=key)


def _already_prepared(record):
    if record is None or record.controller_action is None:
        return False
    if record.state != 'READY':
        raise ValueError('The saved controller edit is incomplete. Its Action was retained for recovery.')
    return True


def _is_fk(action):
    get = getattr(action, 'get', None)
    return get is not None and get('character_designer_controller_representation') == 'FK'


def _needs_full_ik(record):
    return (_already_prepared(record) and _is_fk(record.controller_action)
            and getattr(record, 'alternate_action', None) is None)


_POLE_LAYOUTS = {'knees': 'character_designer_knee_pole_layout',
                 'elbows': 'character_designer_elbow_pole_layout'}


def _needs_pole_alignment(record, kind):
    return (_already_prepared(record) and not _is_fk(record.controller_action)
            and not record.controller_action.get(_POLE_LAYOUTS[kind]))


def needs_knee_alignment(record):
    return _needs_pole_alignment(record, 'knees')


def needs_elbow_alignment(record):
    return _needs_pole_alignment(record, 'elbows')


def prepare_motion(context, home, clip_key, *, from_fk=False, align_knees=False, align_elbows=False):
    rows = [row for row in _eligible_rows(home) if row.clip_key == clip_key]
    if len(rows) != 1:
        raise ValueError('This motion is no longer available for controller preparation.')
    record = _record(home, rows[0], create=False)
    if align_knees or align_elbows:
        kind = 'elbows' if align_elbows else 'knees'
        if from_fk or (align_knees and align_elbows) or not _needs_pole_alignment(record, kind):
            raise ValueError('Choose a retained IK motion that needs ' + kind[:-1] + ' pole alignment.')
        return _enqueue(context, home, rows, retry=True, daily_edit=False, pole_alignment=kind)
    if from_fk:
        if not _needs_full_ik(record):
            raise ValueError('Choose a prepared FK edit without a full IK version.')
        return _enqueue(context, home, rows, retry=True, daily_edit=False, from_fk=True)
    if _already_prepared(record):
        return dict(state='READY', message='Controllers already prepared. Open the motion to edit them.')
    return _enqueue(context, home, rows, retry=True)


def prepare_missing(context, home):
    rows = list(missing_motions(home))
    settings = context.scene.character_designer_animation_worklist
    choices = settings.items if settings.items else settings.catalog
    index = getattr(settings, 'list_cursor', settings.active_index) if settings.items else settings.catalog_index
    selected = choices[index].clip_key if 0 <= index < len(choices) else None
    rows.sort(key=lambda row: row.clip_key != selected)
    result = _enqueue(context, home, rows)
    if getattr(home.character_designer_animation_worklist, 'precise_imported_motions', False):
        fk_rows = _missing_full_ik(home)
        if fk_rows:
            result = _enqueue(context, home, fk_rows, daily_edit=False, from_fk=True)
    return result


def open_alternate(context, home, clip_key):
    """Swap the preferred and alternate Actions, retaining both editable versions."""
    from . import animation_worklist_edit_scene as edit_scene
    row = edit_scene._motion_row(home, clip_key)
    record = _record(home, row, create=False) if row else None
    if not _already_prepared(record) or record.alternate_action is None:
        raise ValueError('This motion has no alternate controller version.')
    current, alternate = (record.controller_action, record.controller_slot), (record.alternate_action, record.alternate_slot)
    try:
        record.controller_action, record.controller_slot = alternate
        record.alternate_action, record.alternate_slot = current
        return _activate(context, home, record)
    except Exception:
        record.controller_action, record.controller_slot = current
        record.alternate_action, record.alternate_slot = alternate
        raise


def _row_for_pending(item):
    from . import animation_worklist_edit_scene as edit_scene
    home = item['home']
    if home.character_designer_animation_worklist.workspace_identity != item['workspace']:
        raise ValueError('The motion workspace changed during preparation. Its disk result was retained.')
    scene = edit_scene.editing_scene(home)
    matches = [row for row in scene.character_designer_animation_worklist.items
               if row.clip_key == item['clip_key']] if scene is not None else []
    if len(matches) > 1:
        raise ValueError('This motion has multiple source work items. Resolve their identity before preparing it again.')
    row = matches[0] if matches else None
    if row is None or (row.work_item_id or row.clip_key) != item['work_item_id']:
        raise ValueError('The source work item changed during preparation. Its disk result was retained.')
    source_rig, source_action, source_slot = row.rig, row.custom_action, row.custom_slot
    timing = scene.render
    entry = item['entry']
    pole_alignment = entry.get('pole_alignment', '')
    if entry.get('from_fk') or pole_alignment:
        record = _record(home, row, create=False)
        eligible = (_needs_pole_alignment(record, pole_alignment) if pole_alignment
                    else _needs_full_ik(record))
        if not eligible:
            raise ValueError('The chosen controller edit changed during preparation.')
        source_rig, source_action, source_slot = record.target, record.controller_action, record.controller_slot
        timing = home.render
    if (source_rig, source_action, source_slot) != (item['source_rig'], item['source_action'], item['source_slot']):
        raise ValueError('The source work item changed during preparation. Its disk result was retained.')
    if (timing.fps, timing.fps_base) != item['source_timing']:
        raise ValueError('The source motion timing changed during preparation. Prepare again with its current timing.')
    _target(home, item['target'])
    return scene, row


def _start_next(context, key):
    from . import animation_controller_worker as worker
    item = _PENDING[key]
    home = item['home']
    if home.character_designer_animation_worklist.workspace_identity != item['workspace']:
        raise ValueError('The motion workspace changed before preparation started.')
    _target(home, item['target'])
    if item['clip_key'] is None:
        entry = item['remaining'].pop(0)
        item.update(clip_key=entry['clip_key'], name=entry['name'], phase='Preparing motion', entry=entry,
                    outcome=None)
        return  # Let the queued name/progress draw before import or native capture.
    entry = item['entry']
    item['phase'] = 'Importing motion'
    scene, row = _resolve_row(context, home, entry['clip_key'])
    expected_identity = entry['work_item_id'] or ('' if entry['catalog_only'] else entry['clip_key'])
    if expected_identity and (row.work_item_id or row.clip_key) != expected_identity:
        raise ValueError('The queued motion identity changed. Prepare its current entry again.')
    record = _record(home, row, create=False)
    from_fk = entry.get('from_fk', False)
    pole_alignment = entry.get('pole_alignment', '')
    already_prepared = _already_prepared(record)
    retained = from_fk or bool(pole_alignment)
    eligible = (_needs_pole_alignment(record, pole_alignment) if pole_alignment
                else from_fk and _needs_full_ik(record))
    if already_prepared and not eligible:
        _finish_item(key, 'SKIPPED')
        return
    if record is not None and record.state == 'MAPPING_NEEDED' and not entry['retry']:
        item['error'] = record.error
        _finish_item(key, 'MAPPING_NEEDED')
        return
    if retained and (not already_prepared or (record.controller_action, record.controller_slot) != entry['retained_origin']):
        raise ValueError('The retained controller edit is no longer available.')
    source_rig, action, slot = (record.target, record.controller_action, record.controller_slot) if retained else (
        row.rig, row.custom_action, row.custom_slot)
    timing = home.render if retained else scene.render
    item.update(work_item_id=row.work_item_id or row.clip_key, source_rig=source_rig,
                source_action=action, source_slot=slot, source_timing=(timing.fps, timing.fps_base))
    options = {'align_' + pole_alignment: True} if pole_alignment else {}
    with context.temp_override(scene=home, view_layer=item['layer']):
        item['job'] = worker.begin(bpy.context, source_rig, action, slot,
                                   item['target'], source_fps=timing.fps / timing.fps_base,
                                   daily_edit=entry.get('daily_edit', False), **options)
    item['phase'] = 'Preparing inputs'


def cancel_pending(key):
    from . import animation_controller_worker as worker
    item = _PENDING.get(key)
    if item is None:
        return
    item['cancel_requested'] = True
    item['phase'] = 'Cancelling'
    if item['job'] is not None:
        worker.request_cancel(item['job'])
    _start_runtime()
    _redraw_pending()


def _pending_error(item, error):
    job = item.get('job')
    message = str(error) + (' Worker files: ' + str(job['root']) if job is not None else '')
    item['error'], item['phase'] = message, 'Needs attention'
    try:
        settings = item['home'].character_designer_animation_worklist
        settings.status, settings.has_error = message, True
    except (ReferenceError, AttributeError):
        pass


def _finish_item(key, outcome):
    from . import animation_controller_worker as worker
    item = _PENDING[key]
    outcome = item.get('outcome') or outcome
    item['outcome'] = outcome
    job = item.get('job')
    if job is not None:
        if worker.process_running(job):
            return False
        worker.close(job)
    item['job'] = None
    if outcome in {'READY', 'SKIPPED', 'MAPPING_NEEDED', 'ERROR'}:
        field = {'READY': 'completed', 'SKIPPED': 'skipped', 'MAPPING_NEEDED': 'failed', 'ERROR': 'failed'}[outcome]
        item[field] += 1
    item.update(clip_key=None, name='', phase='Queued', outcome=None)
    if item['cancel_requested'] or not item['remaining']:
        try:
            settings = item['home'].character_designer_animation_worklist
            summary = ('Preparation cancelled. ' if item['cancel_requested'] else 'Preparation complete. ')
            summary += '%d ready, %d skipped, %d need attention.' % (item['completed'], item['skipped'], item['failed'])
            settings.status = summary + (' ' + item['error'] if item['error'] else '')
            settings.has_error = bool(item['failed'] or item['error'])
        except (ReferenceError, AttributeError):
            pass
        _PENDING.pop(key, None)
    return True


def _mapping_needed(item, error):
    if item['entry'].get('from_fk') or item['entry'].get('pole_alignment'):
        item['error'] = str(error)
        return  # The existing controller record remains ready to edit.
    _scene, row = _row_for_pending(item)
    record = _record(item['home'], row)
    if record.controller_action is not None:
        raise ValueError('An existing controller edit must be reviewed before preparing again.')
    previous = record.state, record.error
    try:
        record.state, record.error = 'MAPPING_NEEDED', str(error)
    except Exception:
        record.state, record.error = previous
        raise
    item['error'] = str(error)


def _discard_unpublished(action):
    action.use_fake_user = False
    if action.users:
        action.use_fake_user = True
        raise RuntimeError('The unbound controller result acquired another user; it was retained for recovery.')
    try:
        bpy.data.actions.remove(action)
    except Exception:
        action.use_fake_user = True
        raise


def replace_aligned(context, home, record, expected, built, *, kind='knees'):
    """Publish a verified replacement while retaining the prior Action and any alternate."""
    from .animation_local_motion_pins import assign_character_action
    old, old_slot = expected
    if (not _needs_pole_alignment(record, kind) or (record.controller_action, record.controller_slot) != expected
            or not built.action.get(_POLE_LAYOUTS[kind]) or built.action == old):
        raise ValueError('The retained IK edit changed before the aligned Action could be attached.')
    rig = _target(home, record.target)
    ad = rig.animation_data
    active = ad and (ad.action, ad.action_slot_handle) == expected
    if active and (context.scene != home or rig.mode not in {'POSE', 'OBJECT'} or ad.use_tweak_mode
                   or ad.action_blend_type != 'REPLACE' or ad.action_influence != 1
                   or (ad.use_nla and any(not track.mute and track.strips for track in ad.nla_tracks))
                   or (context.screen and context.screen.is_animation_playing)):
        raise ValueError('Stop playback and return to the character to replace its open IK edit.')
    if active:
        from .animation_export_inputs import _curves
        frame = home.frame_current + home.frame_subframe
        for curve in _curves(old, old_slot):
            if curve.mute or (curve.group and curve.group.mute):
                continue
            raw = rig.path_resolve(curve.data_path)
            value = raw if isinstance(raw, (int, float)) else raw[curve.array_index]
            if not math.isclose(value, curve.evaluate(frame), abs_tol=2.e-5, rel_tol=2.e-6):
                raise ValueError('The character has an unkeyed control change. Key it before aligning the open motion.')
    previous_fake = old.use_fake_user
    previous_name = old.name
    new_name = built.action.name
    try:
        old.use_fake_user = True
        if kind == 'elbows':
            old.name = '.Archive · Before Elbow Alignment · ' + previous_name
            built.action.name = previous_name
            if built.action.name != previous_name:
                raise ValueError('The repaired Action could not keep the current motion name.')
        record.controller_action, record.controller_slot = built.action, built.slot
        if active:
            assign_character_action(rig, built.action, built.slot)
            home.frame_set(home.frame_current, subframe=home.frame_subframe)
    except Exception:
        if kind == 'elbows':
            built.action.name = new_name
            old.name = previous_name
        record.controller_action, record.controller_slot = expected
        if active:
            assign_character_action(rig, old, old_slot)
            home.frame_set(home.frame_current, subframe=home.frame_subframe)
        old.use_fake_user = previous_fake
        raise


def _open_requested(context, item, record):
    request = item.get('open_request')
    if not request or request[0] != item['clip_key']:
        return None
    item.pop('open_request')
    home = item['home']
    settings = home.character_designer_animation_worklist
    if (context.window != item['window'] or context.scene != home
            or settings.selected_motion_clip != request[0]
            or (0 <= settings.catalog_index < len(settings.catalog)
                and settings.catalog[settings.catalog_index].clip_key != request[0])):
        return None
    try:
        if _open_context(context, item['target']) == request[1]:
            return _activate(context, home, record)
    except (ValueError, RuntimeError, ReferenceError) as error:
        # Publication already succeeded. A delayed open can fail independently.
        item['error'] = 'Open the prepared motion when ready. ' + str(error)
    return None


def finalize_pending(context, key):
    from . import animation_controller_worker as worker, animation_controller_mapping as mapping
    from . import animation_action_browser as browser
    item = _PENDING.get(key)
    if item is None or item['job'] is None:
        raise ValueError('This controller preparation is no longer pending.')
    if item.get('finalizing'):
        raise RuntimeError('This controller result is already being published.')
    item['finalizing'] = True
    home, job = item['home'], item['job']
    created, record, outcome = None, None, 'ERROR'
    try:
        if item['cancel_requested'] or job['cancel_requested']:
            worker.request_cancel(job)
            outcome = 'CANCELLED'
            return dict(state='CANCELLED', message='Preparation cancelled. Completed controller edits were retained.')
        scene, row = _row_for_pending(item)
        record = _record(home, row, create=False)
        from_fk = item['entry'].get('from_fk', False)
        pole_alignment = item['entry'].get('pole_alignment', '')
        if _already_prepared(record) and not (from_fk or pole_alignment):
            outcome = 'SKIPPED'
            return dict(state='READY', message='The existing controller edit was retained; no result was appended.')
        with context.temp_override(scene=home, view_layer=item['layer']):
            built = worker.finish(job)
        created = built.action
        browser.classify_action(created, item['target'], role='WORK', label=row.name,
                                clip_id=row.work_item_id or row.clip_key)
        suffix = 'FK' if _is_fk(created) else 'IK' if from_fk or pole_alignment else 'Controllers'
        created.name = home.character_designer_animation_worklist.target_name + ' · ' + row.name + ' · ' + suffix
        created.use_fake_user = True
        scene, row = _row_for_pending(item)
        if item['cancel_requested'] or job['cancel_requested']:
            item['cancel_requested'] = True
            _discard_unpublished(created)
            created = None
            outcome = 'CANCELLED'
            return dict(state='CANCELLED', message='Preparation cancelled. Completed controller edits were retained.')
        record = _record(home, row)
        if pole_alignment:
            replace_aligned(context, home, record, item['entry']['retained_origin'], built, kind=pole_alignment)
            outcome = 'READY'
            return dict(state='READY', published=True,
                        message=pole_alignment[:-1].title() + ' poles aligned. The previous Action was retained.')
        if from_fk:
            # The worker validates the retained FK Action and its slot. Only
            # publish the independent alternate after that final check.
            if not _needs_full_ik(record) or (record.controller_action, record.controller_slot) != item['entry']['retained_origin']:
                raise ValueError('The FK edit changed before the IK version could be attached.')
            previous_slot = record.alternate_slot
            try:
                record.alternate_slot = built.slot
                record.alternate_action = created
            except Exception:
                record.alternate_slot = previous_slot
                raise
            outcome = 'READY'
            return dict(state='READY', published=True, message='Full IK version prepared. Both edits are available.')
        if _already_prepared(record):
            if record.controller_action != created:
                _discard_unpublished(created)
            created = None
            outcome = 'SKIPPED'
            return dict(state='READY', message='The existing controller edit was retained.')
        _publish(record, row, built)
        outcome = 'READY'
        opened = _open_requested(context, item, record)
        if opened:
            opened['published'] = True
            return opened
        return dict(state='READY', published=True, message='Controllers prepared. Open this motion to edit them.')
    except mapping.MappingNeeded as exc:
        try:
            _mapping_needed(item, exc)
            outcome = 'MAPPING_NEEDED'
            return dict(state='MAPPING_NEEDED', message=str(exc))
        except Exception as recovery:
            item['cancel_requested'] = True
            _pending_error(item, recovery)
            raise
    except Exception as exc:
        item['cancel_requested'] = True
        _pending_error(item, exc)
        if created is not None and (record is None or record.controller_action != created):
            _discard_unpublished(created)
        raise
    finally:
        item['finalizing'] = False
        try:
            _finish_item(key, outcome)
        except Exception as recovery:
            item['cancel_requested'] = True
            _pending_error(item, recovery)
            raise


def _redraw_pending():
    for wm in bpy.data.window_managers:
        for window in wm.windows:
            if window.screen:
                for area in window.screen.areas:
                    if area.type in {'DOPESHEET_EDITOR', 'VIEW_3D'}:
                        area.tag_redraw()


def _poll_pending():
    global _POLLING
    if _POLLING:
        return _ACTIVE_INTERVAL
    _POLLING = True
    try:
        return _poll_queues()
    finally:
        _POLLING = False


def _poll_queues():
    from . import animation_controller_worker as worker, animation_controller_mapping as mapping
    for key, item in tuple(_PENDING.items()):
        try:
            job = item.get('job')
            window = item['window']
            if not item['cancel_requested'] and not any(
                    candidate == window for candidate in bpy.context.window_manager.windows):
                raise ValueError('The preparation window was closed. Prepare again from the character scene.')
            if job is None:
                if item['cancel_requested']:
                    _finish_item(key, 'CANCELLED')
                    continue
                with bpy.context.temp_override(window=window):
                    _start_next(bpy.context, key)
                continue
            if item['cancel_requested'] and not job['cancel_requested']:
                worker.request_cancel(job)
            result = worker.poll(job)
            if result is None:
                continue
            if result['state'] == 'CANCELLED':
                item['cancel_requested'] = True
                _finish_item(key, 'CANCELLED')
                continue
            if result['state'] != 'EXITED':
                raise RuntimeError('The controller worker returned an unsupported completion state.')
            # Entering an operator evaluates the current view layer. CD's driven
            # helper channels can then differ from the exact inputs just verified.
            # Finish the guarded handoff on this same main-thread turn, then add
            # its own Undo step once the independent Action belongs to the record.
            undo_label = 'Prepare ' + item['name'] + ' Controller Motion'
            # An idle timer can lack an ambient Window. Only infer the clicker's
            # window when it is the sole one. A different or ambiguous active
            # window receives the verified result without an automatic open.
            ambient = bpy.context.window
            if ambient != window and (ambient is not None or len(bpy.context.window_manager.windows) != 1):
                item.pop('open_request', None)
            with bpy.context.temp_override(window=window):
                completed = finalize_pending(bpy.context, key)
            if completed.get('published') and not bpy.app.background and bpy.context.preferences.edit.use_global_undo:
                try:
                    with bpy.context.temp_override(window=window, scene=item['home'], view_layer=item['layer']):
                        if bpy.ops.ed.undo_push(message=undo_label) != {'FINISHED'}:
                            raise RuntimeError('Undo is unavailable in the current context.')
                except (ReferenceError, RuntimeError) as error:
                    settings = item['home'].character_designer_animation_worklist
                    settings.status = 'Controllers prepared. Could not add an Undo step. ' + str(error)
                    settings.has_error = True
        except mapping.MappingNeeded as exc:
            try:
                _mapping_needed(item, exc)
                _finish_item(key, 'MAPPING_NEEDED')
            except Exception as recovery:
                _pending_error(item, recovery)
                _abort_pending(key, item)
        except Exception as exc:
            _pending_error(item, exc)
            _abort_pending(key, item)
    _redraw_pending()
    if _PENDING:
        active = any(item['cancel_requested'] or item.get('job') is None
                     or item['job'].get('state') != 'RUNNING' for item in _PENDING.values())
        return _ACTIVE_INTERVAL if active else _CHILD_INTERVAL
    _remove_handlers()
    return None


def _abort_pending(key, item):
    from . import animation_controller_worker as worker
    item['cancel_requested'] = True
    try:
        if item.get('job') is not None:
            worker.request_cancel(item['job'])
        if key in _PENDING:
            _finish_item(key, 'ERROR')
    except Exception as recovery:
        # Keep the timer and owner until its child/partial append can be safely
        # released; a failed cleanup must never orphan an active process.
        _pending_error(item, recovery)


def _cancel_for_read(_unused):
    for key in tuple(_PENDING):
        try:
            cancel_pending(key)
        except Exception as exc:
            _PENDING[key]['error'] = str(exc)


def _watch_open_action(_scene, depsgraph):
    for item in tuple(_PENDING.values()):
        request = item.get('open_request')
        if request and request[2] and any(
                getattr(update.id, 'original', update.id) == request[2] for update in depsgraph.updates):
            item.pop('open_request', None)


def _remove_handlers():
    if _watch_open_action in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_watch_open_action)
    for name in ('load_pre', 'undo_pre', 'redo_pre'):
        handlers = getattr(bpy.app.handlers, name)
        if _cancel_for_read in handlers:
            handlers.remove(_cancel_for_read)


def _start_runtime():
    bpy.app.handlers.persistent(_cancel_for_read)
    added = []
    registered = bpy.app.timers.is_registered(_poll_pending)
    try:
        if _watch_open_action not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(_watch_open_action)
        for name in ('load_pre', 'undo_pre', 'redo_pre'):
            handlers = getattr(bpy.app.handlers, name)
            if _cancel_for_read not in handlers:
                handlers.append(_cancel_for_read)
                added.append(handlers)
        if not registered:
            bpy.app.timers.register(_poll_pending, first_interval=_ACTIVE_INTERVAL, persistent=True)
    except Exception:
        if not registered and _watch_open_action in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_watch_open_action)
        for handlers in added:
            handlers.remove(_cancel_for_read)
        if not registered and bpy.app.timers.is_registered(_poll_pending):
            bpy.app.timers.unregister(_poll_pending)
        raise


def stop_runtime():
    _cancel_for_read(None)
    if not _PENDING:
        _remove_handlers()
        if bpy.app.timers.is_registered(_poll_pending):
            bpy.app.timers.unregister(_poll_pending)
