"""Owned Blender worker for controller mapping from current RAM.

The parent writes an ID library and appends one verified Action. All scene
evaluation happens in the separately owned background process.
"""
import hashlib
import importlib
import json
import marshal
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from array import array
from functools import lru_cache

import bpy

SCHEMA = 'character-designer.controller-worker/1'
_ACTIVE = None
_JOBS = {}
POLL_BUDGET_SECONDS = .020
_MAX_POLL_STEPS = 4096
_TURN = object()
_SOURCE_COLUMNS = 'native-fcurve-columns-v1'


class StaleInputs(ValueError):
    """The retained worker result no longer matches the current artist inputs."""


def _require(value, message):
    if not value:
        raise RuntimeError(message)


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf8')).hexdigest()


def _write(path, value, *, heartbeat=False):
    path = Path(path)
    pending = path.with_name(path.name + '.pending')
    if heartbeat:
        assert path.name in {'process.json', 'progress.json'}
    replacing = False
    try:
        # Each heartbeat has a single writer. Reuse its complete or partial
        # staging file after a busy reader prevented the preceding replacement.
        with pending.open('w' if heartbeat else 'x', encoding='utf8') as stream:
            stream.write(_json(value))
        replacing = True
        pending.replace(path)
    except OSError as error:
        winerror = getattr(error, 'winerror', None)
        if heartbeat and (winerror in (32, 33) or (winerror == 5 and replacing
                         and path.is_file() and os.access(path, os.W_OK))):
            return False
        raise
    if heartbeat:
        return True


class _ChildProgress:
    """Child-only progress. The parent watcher can publish a heartbeat during one long sample."""
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.phase = ''
        self.sample = self.total = self.round = 0
        self.updated = self.written = 0.
        self.phase_started = time.monotonic()
        self.elapsed_by_phase = {}

    def report(self, phase, sample=0, total=0, round_number=0):
        with self.lock:
            now = time.monotonic()
            changed = (phase, round_number) != (self.phase, self.round)
            if changed and self.phase:
                elapsed = now - self.phase_started
                self.elapsed_by_phase[self.phase] = self.elapsed_by_phase.get(self.phase, 0.) + elapsed
            if changed:
                self.phase_started = now
            self.phase, self.sample, self.total, self.round = phase, sample, total, round_number
            self.updated = time.time()
            if changed or now - self.written >= 1.:
                self._publish(now)

    def heartbeat(self):
        with self.lock:
            now = time.monotonic()
            if self.phase and now - self.written >= 5.:
                self._publish(now)

    def timings(self):
        with self.lock:
            totals = dict(self.elapsed_by_phase)
            if self.phase:
                totals[self.phase] = totals.get(self.phase, 0.) + (time.monotonic() - self.phase_started)
            return totals

    def _publish(self, now):
        self.written = now
        path = self.root / 'progress.json'
        _write(path, dict(job=self.root.name, phase=self.phase, sample=self.sample, total=self.total,
                          round=self.round, updated=self.updated, heartbeat=time.time()), heartbeat=True)


def _package_hashes(package):
    return {path.relative_to(package).as_posix(): _sha(path)
            for path in sorted(package.rglob('*.py'))}


def _resources(path):
    reading = dict(disk=shutil.disk_usage(path)._asdict(), available_bytes=None, used_percent=None)
    if os.name == 'nt':
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                        ('total', ctypes.c_ulonglong), ('available', ctypes.c_ulonglong),
                        ('page_total', ctypes.c_ulonglong), ('page_available', ctypes.c_ulonglong),
                        ('virtual_total', ctypes.c_ulonglong), ('virtual_available', ctypes.c_ulonglong),
                        ('extended', ctypes.c_ulonglong)]
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        _require(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)),
                 'Cannot read actual available memory before controller conversion.')
        reading.update(available_bytes=int(memory.available), used_percent=int(memory.load))
    elif hasattr(os, 'sysconf'):
        try:
            reading['available_bytes'] = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError):
            pass
    _require(reading['available_bytes'] is None or reading['available_bytes'] >= 209715200,
             'Actual available memory is below 200 MB; retry controller conversion after memory is available.')
    return reading


def _windows_process(pid):
    """Open one exact process object and retain its creation identity."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
    _require(handle, 'The controller worker cannot authenticate its parent process.')
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        _require(kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)),
                 'The controller worker cannot read its parent process creation time.')
        created = (int(times[0].dwHighDateTime) << 32) | int(times[0].dwLowDateTime)
        return kernel, handle, created
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def _parent_identity():
    identity = dict(pid=os.getpid())
    if os.name == 'nt':
        kernel, handle, created = _windows_process(identity['pid'])
        try:
            identity['windows_creation'] = created
        finally:
            kernel.CloseHandle(handle)
    return identity


def _parent_watch_backend(identity):
    if os.name == 'nt':
        kernel, handle, created = _windows_process(identity['pid'])
        if created != identity.get('windows_creation'):
            kernel.CloseHandle(handle)
            raise RuntimeError('The controller worker parent PID was reused; the result was refused.')
        def exited():
            status = kernel.WaitForSingleObject(handle, 250)
            _require(status in (0, 258), 'The controller parent process handle became unreadable.')
            return status == 0
        return exited, lambda: kernel.CloseHandle(handle)
    _require(os.getppid() == identity['pid'], 'The controller worker no longer has its initiating parent.')
    def exited():
        time.sleep(.25)
        return os.getppid() != identity['pid']
    return exited, lambda: None


def _watch_parent(request, root, heartbeat=None):
    """Child only: no bpy calls, and termination applies only to this process."""
    identity = request['parent_identity']
    _require(identity['pid'] == request['parent_pid'] and identity['pid'] != os.getpid(),
             'The controller worker parent identity differs from its request.')
    exited, close = _parent_watch_backend(identity)
    stop = threading.Event()
    def run():
        try:
            while not stop.is_set():
                if exited():
                    try:
                        _write(root / 'parent_exit.json', dict(schema=SCHEMA, job=root.name,
                                                               state='PARENT_EXITED', parent=identity))
                    except BaseException:
                        pass
                    os._exit(1)
                    return
                if heartbeat is not None:
                    heartbeat()
        except BaseException as exc:
            try:
                _write(root / 'parent_watch_error.json', dict(schema=SCHEMA, job=root.name, error=str(exc)))
            except BaseException:
                pass
            os._exit(1)
        finally:
            close()
    thread = threading.Thread(target=run, name='Controller worker parent', daemon=True)
    try:
        thread.start()
    except BaseException:
        close()
        raise
    def finish():
        stop.set()
        thread.join()
    return finish


def _value(value, depth=0):
    _require(depth <= 24, 'Controller ownership metadata exceeds the supported depth.')
    if isinstance(value, bpy.types.ID):
        library = value.library
        return dict(id_type=value.bl_rna.identifier, name=value.name_full,
                    library=str(Path(bpy.path.abspath(library.filepath)).resolve()) if library else None)
    if isinstance(value, (bpy.types.Bone, bpy.types.PoseBone)):
        return dict(rna_type=value.bl_rna.identifier, owner=_value(value.id_data, depth + 1), name=value.name)
    if value is None or type(value) in (str, int, bool, float):
        return value
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, 'items'):
        return {str(k): _value(v, depth + 1) for k, v in value.items()}
    if hasattr(value, 'to_list'):
        return _value(value.to_list(), depth + 1)
    if isinstance(value, (tuple, list)) or hasattr(value, '__len__'):
        return [_value(v, depth + 1) for v in value]
    raise RuntimeError('Controller ownership metadata contains an unsupported value: ' + type(value).__name__)


def _rna(block, *, exclude=(), collections=False, depth=0):
    _require(depth <= 16, 'Controller RNA dependencies exceed the supported depth.')
    out = {}
    for prop in block.bl_rna.properties:
        name = prop.identifier
        if name == 'rna_type' or name in exclude:
            continue
        if prop.type == 'COLLECTION':
            if collections:
                out[name] = [_rna(item, collections=True, depth=depth + 1) for item in getattr(block, name)]
        elif not prop.is_readonly and prop.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM', 'POINTER'}:
            out[name] = _value(getattr(block, name))
    return out


def _curve(curve):
    out = _rna(curve, exclude={'group', 'driver'})
    out['keys'] = [_rna(point) for point in curve.keyframe_points]
    out['samples'] = [_rna(point) for point in curve.sampled_points]
    out['modifiers'] = [_rna(modifier, collections=True) for modifier in curve.modifiers]
    if curve.driver is not None:
        out['driver'] = _rna(curve.driver, collections=True)
    return out


def _strip(strip):
    return dict(settings=_rna(strip, exclude={'action_slot'}), slot=getattr(strip, 'action_slot_handle', 0),
                curves=[_curve(curve) for curve in strip.fcurves],
                strips=[_strip(child) for child in strip.strips])


def _animation(ad):
    if ad is None:
        return None
    return dict(action=_value(ad.action), slot=ad.action_slot_handle, use_nla=ad.use_nla,
                blend=ad.action_blend_type, influence=ad.action_influence, extrapolation=ad.action_extrapolation,
                drivers=[_curve(curve) for curve in ad.drivers],
                nla=[dict(settings=_rna(track), strips=[_strip(strip) for strip in track.strips])
                     for track in ad.nla_tracks])


def _control_fingerprint(source, target):
    from . import animation_controller_isolation as isolation, animation_controller_mapping as mapping
    graph = isolation._graph(target)
    _, secondary = isolation._secondary_sources(target)
    objects = {source, target, *secondary}
    if graph:
        objects.update(graph['collection'].objects)
    rows = []
    for obj in sorted(objects, key=lambda value: value.name_full):
        row = dict(id=_value(obj), parent=_value(obj.parent), parent_type=obj.parent_type,
                   parent_bone=obj.parent_bone, parent_inverse=_value(obj.matrix_parent_inverse),
                   basis=_value(obj.matrix_basis),
                   delta={key: _value(getattr(obj, key)) for key in (
                       'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')},
                   properties=_value(dict(obj.items())), animation=_animation(obj.animation_data),
                   constraints=[_rna(con, collections=True) for con in obj.constraints])
        if obj in (source, target):
            row['mapping_world'] = _value(mapping._current_world(obj))
        if obj.type == 'ARMATURE':
            row.update(data=_value(obj.data), data_properties=_value(dict(obj.data.items())),
                       pose_position=obj.data.pose_position, data_animation=_animation(obj.data.animation_data),
                       bones=[(bone.name, bone.use_local_location, bone.bbone_segments,
                               _value(dict(bone.items()))) for bone in obj.data.bones],
                       pose=[(bone.name, _value(dict(bone.items())), _value(bone.custom_shape),
                              _value(bone.custom_shape_transform),
                              [_rna(con, collections=True) for con in bone.constraints]) for bone in obj.pose.bones],
                       collections=[(col.name, _value(dict(col.items())), [b.name for b in col.bones])
                                    for col in obj.data.collections_all])
        rows.append(row)
    return _digest(rows)


def _action_hash(action, slot):
    from . import animation_worklist_fingerprint as fingerprint
    proof = fingerprint.fingerprint_action(action, slot)
    _require(proof.get('fingerprint_known') is True,
             'The worker input Action cannot be verified: ' + proof.get('proof_reason', 'unknown'))
    return proof['action_fingerprint']


def _raw_packet(rig):
    from . import controller_animation as controller
    raw = controller._raw(rig, tuple(rig.pose.bones.keys()))
    return dict(object=raw['object'], pose=raw['pose'],
                constraints=[[bone, name, settings] for (bone, name), settings in raw['constraints'].items()])


def _restore_packet(rig, packet):
    from . import controller_animation as controller
    raw = dict(object=packet['object'], pose=packet['pose'],
               constraints={(bone, name): settings for bone, name, settings in packet['constraints']})
    controller._restore_raw(rig, raw)


def _portable(source, action, slot, target, *, action_hash=None):
    from . import animation_controller_mapping as mapping
    return dict(source_action=(action_hash or _action_hash)(action, slot), source_slot=int(slot),
                source_metadata=_digest(_value(dict(action.items()))),
                source_rest=mapping._rest_fingerprint(source), target_rest=mapping._rest_fingerprint(target),
                controls=_control_fingerprint(source, target))


def _object_constraint_switches(owners):
    """Object constraints can be muted by the deferred CD load lifecycle."""
    return [(obj.name_full, _value(obj), [(c.name, c.type, c.mute) for c in obj.constraints])
            for obj in owners if isinstance(obj, bpy.types.Object) and obj.constraints]


def _restore_object_constraint_switches(rows):
    for name, identity, constraints in rows:
        owner = next((obj for obj in bpy.data.objects if obj.name_full == name and _value(obj) == identity), None)
        _require(owner is not None, 'The snapshot lost a controller constraint owner: ' + name)
        for key, kind, mute in constraints:
            constraint = owner.constraints.get(key)
            _require(constraint is not None and constraint.type == kind and type(mute) is bool,
                     'The snapshot lost a controller constraint on ' + name + ': ' + key)
            if constraint.mute != mute:
                constraint.mute = mute


def _binding(rig):
    ad = rig.animation_data
    if ad is None:
        return None
    return (ad.action.as_pointer() if ad.action else None, ad.action_slot_handle,
            ad.use_nla, ad.action_blend_type, ad.action_influence, ad.action_extrapolation,
            tuple((track.as_pointer(), track.name, track.mute,
                   tuple((strip.as_pointer(), strip.action.as_pointer() if strip.action else None,
                          getattr(strip, 'action_slot_handle', 0)) for strip in track.strips))
                  for track in ad.nla_tracks))


def _witness(context, source, action, slot, target):
    """Read current inputs without seeking or evaluating a scene."""
    from . import animation_controller_mapping as mapping
    return dict(portable=_portable(source, action, slot, target),
                filepath=bpy.data.filepath, dirty=bpy.data.is_dirty,
                scene=context.scene.as_pointer(), view_layer=context.view_layer.as_pointer(),
                mode=context.mode, active=context.view_layer.objects.active.as_pointer()
                if context.view_layer.objects.active else None,
                selected=sorted(obj.as_pointer() for obj in context.selected_objects),
                scenes=[(s.as_pointer(), s.frame_current, s.frame_subframe, s.frame_start,
                         s.frame_end, s.frame_preview_start, s.frame_preview_end, s.use_preview_range,
                         s.tool_settings.use_keyframe_insert_auto) for s in bpy.data.scenes],
                rigs=[(rig.as_pointer(), rig.data.as_pointer(), rig.name, _binding(rig),
                       mapping._rest_fingerprint(rig), _digest(_raw_packet(rig)))
                      for rig in bpy.data.objects if rig.type == 'ARMATURE'])


def _same_witness(before, after, *, appended=False):
    if appended:
        after = dict(after, dirty=before['dirty'])
    return before == after


def _scene_inputs(scene):
    return dict(fps=scene.render.fps, fps_base=scene.render.fps_base,
                unit_system=scene.unit_settings.system, unit_scale=scene.unit_settings.scale_length,
                frame=scene.frame_current + scene.frame_subframe)


def _fresh_inputs(context, source, action, slot, target):
    from . import animation_controller_isolation as isolation
    graph = isolation._graph(target)
    _, secondary = isolation._secondary_sources(target)
    rigs = {source, target, *(obj for obj in secondary if obj.type == 'ARMATURE')}
    if graph:
        rigs.update(obj for obj in graph['collection'].objects if obj.type == 'ARMATURE')
    return dict(portable=_portable(source, action, slot, target), scene=_scene_inputs(context.scene),
                identities=(context.scene.as_pointer(), source.as_pointer(), source.data.as_pointer(),
                            action.as_pointer(), target.as_pointer(), target.data.as_pointer()),
                raw={rig.name_full: _digest(_raw_packet(rig)) for rig in rigs})


def _phase(job, name):
    job['phase'] = name


def _digest_steps(value):
    """Keep canonical JSON and slice large containers, using the native JSON encoder for rows."""
    digest = hashlib.sha256()
    def write(item, depth):
        if depth >= 3 or not isinstance(item, (dict, list, tuple)):
            digest.update(_json(item).encode('utf8'))
            return
        mapping = isinstance(item, dict)
        digest.update(b'{' if mapping else b'[')
        entries = sorted(item) if mapping else item
        for index, entry in enumerate(entries):
            if index:
                digest.update(b',')
            if mapping:
                digest.update(_json(entry).encode('utf8'))
                digest.update(b':')
            yield from write(item[entry] if mapping else entry, depth + 1)
            if index % 32 == 31:
                yield
        digest.update(b'}' if mapping else b']')
    yield from write(value, 0)
    return digest.hexdigest()


def _sha_steps(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data := stream.read(262144):
            digest.update(data)
            yield
    return digest.hexdigest()


def _write_steps(path, value):
    path = Path(path)
    pending = path.with_name(path.name + '.pending')
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    with pending.open('x', encoding='utf8') as stream:
        chunks, size = [], 0
        for chunk in encoder.iterencode(value):
            chunks.append(chunk)
            size += len(chunk)
            if size >= 16384:
                stream.write(''.join(chunks))
                chunks, size = [], 0
                yield
        stream.write(''.join(chunks))
    pending.replace(path)


def _value_steps(value, depth=0):
    _require(depth <= 24, 'Controller ownership metadata exceeds the supported depth.')
    if isinstance(value, (bpy.types.ID, bpy.types.Bone, bpy.types.PoseBone)):
        return _value(value, depth)
    if value is None or type(value) in (str, int, bool, float):
        return value
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, 'items'):
        out = {}
        for key, item in value.items():
            out[str(key)] = yield from _value_steps(item, depth + 1)
            yield
        return out
    if hasattr(value, 'to_list'):
        value = value.to_list()
    if isinstance(value, (tuple, list)) or hasattr(value, '__len__'):
        out = []
        for item in value:
            out.append((yield from _value_steps(item, depth + 1)))
            yield
        return out
    raise RuntimeError('Controller ownership metadata contains an unsupported value: ' + type(value).__name__)


def _rna_steps(block, *, exclude=(), collections=False, depth=0):
    _require(depth <= 16, 'Controller RNA dependencies exceed the supported depth.')
    out = {}
    for prop in block.bl_rna.properties:
        name = prop.identifier
        if name == 'rna_type' or name in exclude:
            continue
        if prop.type == 'COLLECTION':
            if collections:
                rows = []
                for item in getattr(block, name):
                    rows.append((yield from _rna_steps(item, collections=True, depth=depth + 1)))
                out[name] = rows
        elif not prop.is_readonly and prop.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM', 'POINTER'}:
            out[name] = yield from _value_steps(getattr(block, name))
        yield
    return out


def _curve_steps(curve):
    out = yield from _rna_steps(curve, exclude={'group', 'driver'})
    for name, values in (('keys', curve.keyframe_points), ('samples', curve.sampled_points)):
        out[name] = []
        for point in values:
            out[name].append((yield from _rna_steps(point)))
    out['modifiers'] = []
    for modifier in curve.modifiers:
        out['modifiers'].append((yield from _rna_steps(modifier, collections=True)))
    if curve.driver is not None:
        out['driver'] = yield from _rna_steps(curve.driver, collections=True)
    return out


def _strip_steps(strip):
    out = dict(settings=(yield from _rna_steps(strip, exclude={'action_slot'})),
               slot=getattr(strip, 'action_slot_handle', 0), curves=[], strips=[])
    for curve in strip.fcurves:
        out['curves'].append((yield from _curve_steps(curve)))
    for child in strip.strips:
        out['strips'].append((yield from _strip_steps(child)))
    return out


def _animation_steps(ad):
    if ad is None:
        return None
    out = dict(action=_value(ad.action), slot=ad.action_slot_handle, use_nla=ad.use_nla,
               blend=ad.action_blend_type, influence=ad.action_influence,
               extrapolation=ad.action_extrapolation, drivers=[], nla=[])
    for curve in ad.drivers:
        out['drivers'].append((yield from _curve_steps(curve)))
    for track in ad.nla_tracks:
        row = dict(settings=(yield from _rna_steps(track)), strips=[])
        for strip in track.strips:
            row['strips'].append((yield from _strip_steps(strip)))
        out['nla'].append(row)
    return out


def _action_stream_steps(value):
    """The existing Action proof's exact canonical bytes and bounds, yielded per chunk."""
    from . import animation_worklist_fingerprint as fingerprint
    digest = hashlib.sha256()
    point_count = 0
    @lru_cache(maxsize=8192)
    def number(value):
        if not math.isfinite(value):
            raise fingerprint.FingerprintUnknown('Fingerprint payload contains a non-finite number.')
        return ('0.0' if value == 0. else repr(value)).encode('ascii')
    def write(item, depth=0, field=''):
        nonlocal point_count
        if depth > fingerprint.MAX_DEPTH:
            raise fingerprint.FingerprintUnknown('Fingerprint payload exceeds its bounded depth.')
        if isinstance(item, _PointRows):
            count, fields = item.capture
            if count > fingerprint._SEQUENCE_LIMITS[field] or point_count + count > fingerprint.MAX_ACTION_POINTS:
                raise fingerprint.FingerprintUnknown('Action exceeds its key/sample point bound.')
            # Unusual RNA stays on the general serializer with its full per-row
            # size/depth checks. Native point schemas fit well within those bounds.
            if (depth + 3 > fingerprint.MAX_DEPTH or len(fields) > 64 or
                    any(f[1] == 'PLAIN' or f[2] > 16 or f[4] for f in fields)):
                yield from write(fingerprint._StreamSequence(_captured_points(item.capture)), depth, field)
                return
            point_count += count
            digest.update(b'[')
            for chunk in _point_bytes(count, fields, number):
                digest.update(chunk)
                yield
            digest.update(b']')
        elif type(item) is dict:
            if len(item) > fingerprint.MAX_ITEMS or any(type(key) is not str for key in item):
                raise fingerprint.FingerprintUnknown('Fingerprint dictionary exceeds its bound or has non-string keys.')
            digest.update(b'{')
            for index, key in enumerate(sorted(item)):
                if index:
                    digest.update(b',')
                digest.update(fingerprint.canonical_bytes(key))
                digest.update(b':')
                yield from write(item[key], depth + 1, key)
            digest.update(b'}')
        elif type(item) in (list, tuple) or isinstance(item, fingerprint._StreamSequence):
            values = item.values if isinstance(item, fingerprint._StreamSequence) else item
            digest.update(b'[')
            for index, child in enumerate(fingerprint._bounded(
                    values, fingerprint._SEQUENCE_LIMITS.get(field, fingerprint.MAX_ITEMS), field)):
                if field in {'keyframe_points', 'sampled_points'}:
                    point_count += 1
                    if point_count > fingerprint.MAX_ACTION_POINTS:
                        raise fingerprint.FingerprintUnknown('Action exceeds its total key/sample point bound.')
                if index:
                    digest.update(b',')
                if field in fingerprint._CHUNK_SEQUENCES:
                    chunk = fingerprint._plain(child, depth=depth + 1)
                    digest.update(json.dumps(chunk, ensure_ascii=False, allow_nan=False,
                                             sort_keys=True, separators=(',', ':')).encode('utf8'))
                    yield
                else:
                    yield from write(child, depth + 1)
            digest.update(b']')
        else:
            digest.update(fingerprint.canonical_bytes(item))
            yield
    yield from write(value)
    return digest.hexdigest()


def _point_capture(points, exclude):
    """Read native point arrays once; the later canonical hash reads only bytes."""
    from . import animation_worklist_fingerprint as fingerprint
    if not points:
        return (0, ())
    fields = []
    fallback_rows = None
    for prop in points[0].bl_rna.properties:
        name, kind = prop.identifier, prop.type
        if name == 'rna_type' or name in exclude:
            continue
        is_array = bool(getattr(prop, 'is_array', False))
        width = prop.array_length if is_array else 1
        enums = tuple((item.value, item.identifier) for item in prop.enum_items) if kind == 'ENUM' else ()
        # Native FCurve point coordinates, handles and easing parameters are
        # Blender floats. foreach_get accepts a matching C float buffer.
        code = 'f' if kind == 'FLOAT' else 'i'
        try:
            if kind not in {'FLOAT', 'INT', 'BOOLEAN', 'ENUM'}:
                raise TypeError(kind)
            if sum(bool(n) for n in getattr(prop, 'array_dimensions', ())) > 1:
                raise TypeError('multidimensional')
            data = array(code, [0]) * (len(points) * width)
            points.foreach_get(name, data)
            fields.append((name, kind, width, is_array, bool(getattr(prop, 'is_enum_flag', False)),
                           enums, code, data.tobytes()))
        except (TypeError, ValueError, RuntimeError):
            # Preserve the old schema for fields without a native bulk getter;
            # never silently omit a property or accept a partial Action proof.
            if fallback_rows is None:
                schema = {}
                fallback_rows = [fingerprint._rna_settings(point, exclude=exclude, schema_cache=schema)
                                 for point in points]
            fields.append((name, 'PLAIN', tuple(row[name] for row in fallback_rows)))
    return (len(points), tuple(fields))


def _captured_points(capture):
    count, fields = capture
    values = []
    for field in fields:
        if field[1] == 'PLAIN':
            values.append((field, field[2], None))
        else:
            data = array(field[6])
            data.frombytes(field[7])
            values.append((field, data, dict(field[5])))
    for index in range(count):
        row = {}
        for field, data, enum_names in values:
            name, kind = field[:2]
            if kind == 'PLAIN':
                row[name] = data[index]
                continue
            _, _, width, is_array, enum_flags, enums, _code, _bytes = field
            def scalar(value):
                if kind == 'ENUM':
                    return (sorted(label for flag, label in enums if flag & int(value))
                            if enum_flags else enum_names[int(value)])
                return bool(value) if kind == 'BOOLEAN' else value
            row[name] = ([scalar(value) for value in data[index*width:(index+1)*width]]
                         if is_array else scalar(data[index]))
        yield row


class _PointRows:
    def __init__(self, capture):
        self.capture = capture


def _point_bytes(count, fields, number):
    """Encode bounded native point columns with exactly the canonical JSON layout."""
    from . import animation_worklist_fingerprint as fingerprint
    static = b'{'
    fields_by_row = []
    for index, field in enumerate(sorted(fields, key=lambda f: f[0])):
        name, kind, width, is_array, _, enums, code, buffer = field
        static += (b',' if index else b'') + fingerprint.canonical_bytes(name) + b':'
        values = memoryview(buffer).cast(code)
        if not count:
            continue
        if kind == 'FLOAT':
            encode = number
        elif kind == 'ENUM':
            encode = {key: fingerprint.canonical_bytes(label) for key, label in enums}.__getitem__
        elif kind == 'BOOLEAN':
            encode = lambda value: b'true' if value else b'false'
        else:
            encode = lambda value: str(value).encode('ascii')
        def at(i, values=values, encode=encode, width=width, is_array=is_array):
            if is_array and width == 2:
                return b'[' + encode(values[2*i]) + b',' + encode(values[2*i+1]) + b']'
            if is_array:
                return b'[' + b','.join(encode(v) for v in values[i*width:(i+1)*width]) + b']'
            return encode(values[i])
        stride = width * values.itemsize
        if buffer == buffer[:stride] * count:
            static += at(0)
        else:
            fields_by_row.append((static, at))
            static = b''
    end = static + b'}'
    for first in range(0, count, 64):
        chunk = []
        for i in range(first, min(first + 64, count)):
            if i:
                chunk.append(b',')
            for prefix, at in fields_by_row:
                chunk.extend((prefix, at(i)))
            chunk.append(end)
        yield b''.join(chunk)


def _action_capture(action, slot):
    """Exact selected-slot content snapshot without scene evaluation or ID writes."""
    from . import animation_worklist_fingerprint as fingerprint
    native_curves = fingerprint._selected_curves(action, slot)
    fingerprint._check_action_point_budget(native_curves)
    native = iter(native_curves)
    view = fingerprint._action_view(action, slot, streamed=True)
    layers = []
    for layer in view['layers'].values:
        for strip in layer['strips']:
            rows = []
            for row in strip['curves'].values:
                curve = next(native)
                row['keyframe_points'] = _point_capture(curve.keyframe_points, fingerprint._KEY_UI)
                row['sampled_points'] = _point_capture(curve.sampled_points, {'select'})
                row['modifiers'] = tuple(row['modifiers'].values)
                rows.append(row)
            strip['curves'] = rows
        layers.append(layer)
    _require(next(native, None) is None, 'The selected Action curve inventory changed during capture.')
    view['layers'] = layers
    return view


def _captured_action(capture):
    from . import animation_worklist_fingerprint as fingerprint
    layers = []
    for layer in capture['layers']:
        strips = []
        for strip in layer['strips']:
            curves = []
            for original in strip['curves']:
                row = dict(original)
                for key in ('keyframe_points', 'sampled_points'):
                    row[key] = _PointRows(row[key])
                curves.append(row)
            strips.append(dict(strip, curves=curves))
        layers.append(dict(layer, strips=strips))
    return dict(capture, layers=layers)


def _action_hash_steps(action, slot, captures=None):
    from . import animation_worklist_fingerprint as fingerprint
    try:
        started = time.monotonic()
        capture = _action_capture(action, slot)
        key = (action.as_pointer(), slot)
        capture_digest = None
        if captures is not None:
            captures['values'][key] = capture
            captures['timings'].append(dict(phase='Native Action input capture', action=action.name,
                                            seconds=time.monotonic() - started))
        yield _TURN  # Draw progress/cancel before hashing the immutable payload.
        if captures is not None and captures.get('source_columns') == key:
            # The parent and private child use the same pinned Blender runtime.
            # Bind their exact typed RNA columns, including coordinates, handles,
            # enum tables and settings. The persisted Action provenance remains
            # the independently computed canonical worklist SHA in the child.
            def ordered(value):
                if isinstance(value, dict):
                    return {k: ordered(value[k]) for k in sorted(value)}
                if isinstance(value, tuple):
                    return tuple(ordered(v) for v in value)
                if isinstance(value, list):
                    return [ordered(v) for v in value]
                return value
            binary = marshal.dumps(ordered(capture), 2)
            return _SOURCE_COLUMNS + ':' + hashlib.sha256(binary).hexdigest()
        if captures is not None:
            # Hash a freshly read typed capture. Version 2 encodes values without
            # sharing identities; metadata type and signed zero remain distinct.
            # This memo is private to one job. Only the canonical hash is published.
            try:
                capture_digest = hashlib.sha256(marshal.dumps(capture, 2)).digest()
            except (TypeError, ValueError, OverflowError):
                pass
            memo = captures.setdefault('canonical_hashes', {})
            previous = memo.get(key)
            if capture_digest is not None and previous and previous[0] == capture_digest:
                return previous[1]
        payload = _captured_action(capture)
        result = yield from _action_stream_steps({'schema': fingerprint.SCHEMA, 'action': payload})
        if capture_digest is not None:
            memo[key] = (capture_digest, result)
        return result
    except (fingerprint.FingerprintUnknown, AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError('The worker input Action cannot be verified: ' + str(exc)) from exc


def make_action_hasher(*, source_columns=None):
    """Scoped to one background job. Re-read the native curves at every boundary."""
    captures = dict(values={}, timings=[], source_columns=source_columns)

    def action_hash(action, slot):
        iterator = _action_hash_steps(action, slot, captures)
        while True:
            try:
                next(iterator)
            except StopIteration as result:
                return result.value
    return action_hash


def _rest_steps(rig):
    rows = {}
    for bone in rig.data.bones:
        rows[bone.name] = dict(parent=bone.parent.name if bone.parent else None,
            matrix=[list(row) for row in bone.matrix_local], tail=list(bone.tail_local), connected=bone.use_connect,
            inherit_rotation=bone.use_inherit_rotation, inherit_scale=bone.inherit_scale,
            rotation_mode=rig.pose.bones[bone.name].rotation_mode)
        yield
    return (yield from _digest_steps(dict(world=[list(row) for row in rig.matrix_basis], bones=rows)))


def _raw_steps(rig):
    from . import controller_animation as controller, animation_export_inputs as inputs
    def channels(block):
        settings = {key: getattr(block, key) for key in inputs._POSE_SETTINGS if hasattr(block, key)}
        if block is not rig:
            for prop in block.bl_rna.properties:
                if prop.identifier.startswith('bbone_') and not prop.is_readonly and prop.type in {'FLOAT', 'INT', 'BOOLEAN'}:
                    value = getattr(block, prop.identifier)
                    settings[prop.identifier] = tuple(value) if getattr(prop, 'is_array', False) else value
        return dict(rotation_mode=block.rotation_mode, settings=settings,
            **{key: tuple(getattr(block, key)) for key in controller._TRANSFORMS},
            properties={str(key): value for key, value in block.items()
                        if type(value) in (bool, int, float) and math.isfinite(value)})
    out = dict(object=channels(rig), pose={}, constraints=[])
    for bone in rig.pose.bones:
        out['pose'][bone.name] = channels(bone)
        for con in bone.constraints:
            out['constraints'].append([bone.name, con.name,
                {key: value for key, value in inputs._settings(con).items() if key != 'name'}])
            yield
        yield
    return out


def _control_steps(source, target):
    from . import animation_controller_isolation as isolation, animation_controller_mapping as mapping
    graph = isolation._graph(target)
    yield
    _, secondary = isolation._secondary_sources(target)
    objects = {source, target, *secondary}
    if graph:
        objects.update(graph['collection'].objects)
    rows = []
    for obj in sorted(objects, key=lambda value: value.name_full):
        row = dict(id=_value(obj), parent=_value(obj.parent), parent_type=obj.parent_type,
                   parent_bone=obj.parent_bone, parent_inverse=_value(obj.matrix_parent_inverse),
                   basis=_value(obj.matrix_basis),
                   delta={key: _value(getattr(obj, key)) for key in (
                       'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')},
                   properties=(yield from _value_steps(dict(obj.items()))),
                   animation=(yield from _animation_steps(obj.animation_data)), constraints=[])
        for con in obj.constraints:
            row['constraints'].append((yield from _rna_steps(con, collections=True)))
        if obj in (source, target):
            row['mapping_world'] = _value(mapping._current_world(obj))
        if obj.type == 'ARMATURE':
            row.update(data=_value(obj.data), data_properties=(yield from _value_steps(dict(obj.data.items()))),
                       pose_position=obj.data.pose_position, data_animation=(yield from _animation_steps(obj.data.animation_data)),
                       bones=[], pose=[], collections=[])
            for bone in obj.data.bones:
                row['bones'].append((bone.name, bone.use_local_location, bone.bbone_segments,
                                     (yield from _value_steps(dict(bone.items())))))
                yield
            for bone in obj.pose.bones:
                constraints = []
                for con in bone.constraints:
                    constraints.append((yield from _rna_steps(con, collections=True)))
                row['pose'].append((bone.name, (yield from _value_steps(dict(bone.items()))),
                    _value(bone.custom_shape), _value(bone.custom_shape_transform), constraints))
                yield
            for col in obj.data.collections_all:
                row['collections'].append((col.name, (yield from _value_steps(dict(col.items()))), [b.name for b in col.bones]))
                yield
        rows.append(row)
    return (yield from _digest_steps(rows))


def _portable_steps(source, action, slot, target, captures=None):
    return dict(source_action=(yield from _action_hash_steps(action, slot, captures)), source_slot=int(slot),
                source_metadata=(yield from _digest_steps((yield from _value_steps(dict(action.items()))))),
                source_rest=(yield from _rest_steps(source)), target_rest=(yield from _rest_steps(target)),
                controls=(yield from _control_steps(source, target)))


def _navigation(context):
    """Context is compared adjacent to atomic native I/O, never across user navigation."""
    return dict(filepath=bpy.data.filepath, dirty=bpy.data.is_dirty,
        scene=context.scene.as_pointer(), view_layer=context.view_layer.as_pointer(),
        mode=context.mode, active=context.view_layer.objects.active.as_pointer() if context.view_layer.objects.active else None,
        selected=sorted(obj.as_pointer() for obj in context.selected_objects),
        scenes=[(s.as_pointer(), s.frame_current, s.frame_subframe, s.frame_start, s.frame_end,
                 s.frame_preview_start, s.frame_preview_end, s.use_preview_range, s.tool_settings.use_keyframe_insert_auto)
                for s in bpy.data.scenes])


def _witness_steps(context, source, action, slot, target, raw_out=None, captures=None):
    out = dict(_navigation(context), portable=(yield from _portable_steps(source, action, slot, target, captures)), rigs=[])
    for rig in tuple(bpy.data.objects):
        if rig.type != 'ARMATURE':
            continue
        raw = yield from _raw_steps(rig)
        if raw_out is not None:
            raw_out[rig.name_full] = raw
        out['rigs'].append((rig.as_pointer(), rig.data.as_pointer(), rig.name, _binding(rig),
                           (yield from _rest_steps(rig)), (yield from _digest_steps(raw))))
        yield
    return out


def _fresh_steps(context, source, action, slot, target, captures=None):
    from . import animation_controller_isolation as isolation
    graph = isolation._graph(target)
    yield
    _, secondary = isolation._secondary_sources(target)
    rigs = {source, target, *(obj for obj in secondary if obj.type == 'ARMATURE')}
    if graph:
        rigs.update(obj for obj in graph['collection'].objects if obj.type == 'ARMATURE')
    out = dict(portable=(yield from _portable_steps(source, action, slot, target, captures)), scene=_scene_inputs(context.scene),
               identities=(context.scene.as_pointer(), source.as_pointer(), source.data.as_pointer(),
                           action.as_pointer(), target.as_pointer(), target.data.as_pointer()), raw={})
    for rig in rigs:
        out['raw'][rig.name_full] = yield from _digest_steps((yield from _raw_steps(rig)))
        yield
    return out


def _author_equal(before, after):
    return before['portable'] == after['portable'] and before['rigs'] == after['rigs']


def _track(job, value):
    if value is not None:
        job['watched'].add(value.as_pointer())


def _capture_updates(_scene, depsgraph):
    changed = {getattr(item.id, 'original', item.id).as_pointer() for item in depsgraph.updates}
    for job in tuple(_JOBS.values()):
        if job.get('watched', set()) & changed:
            job['epoch'] += 1


def _cancel_for_read(_unused):
    for job in tuple(_JOBS.values()):
        try:
            request_cancel(job)
            if not process_running(job):
                close(job)
        except Exception as exc:
            job['lifecycle_error'] = str(exc)


def _watch_jobs():
    handlers = bpy.app.handlers
    handlers.persistent(_cancel_for_read)
    for name, callback in (('depsgraph_update_post', _capture_updates), ('load_pre', _cancel_for_read),
                           ('undo_pre', _cancel_for_read), ('redo_pre', _cancel_for_read)):
        hooks = getattr(handlers, name)
        if callback not in hooks:
            hooks.append(callback)


def _unwatch_jobs():
    if _JOBS:
        return
    handlers = getattr(getattr(bpy, 'app', None), 'handlers', None)
    if handlers is None:
        return
    for name, callback in (('depsgraph_update_post', _capture_updates), ('load_pre', _cancel_for_read),
                           ('undo_pre', _cancel_for_read), ('redo_pre', _cancel_for_read)):
        hooks = getattr(handlers, name)
        if callback in hooks:
            hooks.remove(callback)


def _light_inputs(job):
    from .animation_loop_timing import is_loop
    source, target = job['owners']
    return (source.as_pointer(), source.data.as_pointer(), target.as_pointer(), target.data.as_pointer(),
            job['action'].as_pointer(), int(job['slot']), is_loop(job['action']), _binding(source), _binding(target),
            _scene_inputs(job['scene']), source.mode == 'EDIT', target.mode == 'EDIT')


def _guard(job):
    if job['cancel_requested']:
        raise StaleInputs('The controller preparation was cancelled.')
    if _light_inputs(job) != job['light_inputs']:
        raise StaleInputs('The source, character binding or timing changed during preparation. Current inputs were retained; prepare again.')


def _publication_raw(job):
    """Small final author-input fence, independent of deferred depsgraph callbacks.

    The full Action/Rest/ownership proofs are already read cooperatively. Raw
    controls can be assigned directly by another timer without a depsgraph
    callback, so compare their original exact packet once at handoff as well.
    This indivisible read is measured separately from the scheduler target.
    """
    current = {rig.name_full: _digest(_raw_packet(rig)) for rig in job['raw_owners']}
    if current != job['fresh_inputs']['raw']:
        raise StaleInputs('Character control inputs changed before publication. Current work was retained; prepare again.')


def _publication_action(job, action, slot, expected, label):
    if _action_capture(action, slot) != expected:
        raise StaleInputs(label + ' keyframes changed before publication. Current work was retained; prepare again.')


def _publication_fresh(job):
    """Recheck every original freshness field after a late native notification.

    The Action content was just compared against its exact immutable capture,
    so reuse its verified canonical hash rather than rewalking every key in
    Python. Every other field uses the original synchronous proof functions.
    """
    from . import animation_controller_isolation as isolation, animation_controller_mapping as mapping
    source, target = job['owners']
    action, slot = job['action'], job['slot']
    graph = isolation._graph(target)
    _, secondary = isolation._secondary_sources(target)
    rigs = {source, target, *(obj for obj in secondary if obj.type == 'ARMATURE')}
    if graph:
        rigs.update(obj for obj in graph['collection'].objects if obj.type == 'ARMATURE')
    current = dict(portable=dict(source_action=job['fresh_inputs']['portable']['source_action'],
        source_slot=int(slot), source_metadata=_digest(_value(dict(action.items()))),
        source_rest=mapping._rest_fingerprint(source), target_rest=mapping._rest_fingerprint(target),
        controls=_control_fingerprint(source, target)), scene=_scene_inputs(job['scene']),
        identities=(job['scene'].as_pointer(), source.as_pointer(), source.data.as_pointer(),
                    action.as_pointer(), target.as_pointer(), target.data.as_pointer()),
        raw={rig.name_full: _digest(_raw_packet(rig)) for rig in rigs})
    if current != job['fresh_inputs']:
        raise StaleInputs('Inputs changed after result verification. Current work was retained; prepare again.')


def _coherent(job, factory):
    """Discard a torn read instead of restoring or trusting a mixture of author states."""
    for _attempt in range(3):
        epoch = job['epoch']
        value = yield from factory()
        _guard(job)
        if job['epoch'] == epoch:
            job['proof_epoch'] = epoch
            return value
        job['metrics']['capture_retries'] += 1
        yield _TURN
    raise StaleInputs('The character inputs kept changing during verification. Current work was retained; prepare again when ready.')


def _atomic(job, label, callback, *, appended=False):
    before = _navigation(bpy.context)
    start = time.monotonic()
    try:
        return callback()
    finally:
        elapsed = time.monotonic() - start
        job['metrics']['atomic_operations'].append(dict(phase=label, seconds=elapsed))
        after = _navigation(bpy.context)
        if appended:
            after['dirty'] = before['dirty']
        _require(before == after, 'Native ' + label + ' changed the original context or scene timing.')


def conflicting(source, target):
    for job in _JOBS.values():
        try:
            if source in job['owners'] or target in job['owners']:
                return job
        except ReferenceError:
            # Load/undo invalidated the old RNA while its cancelled child exits.
            continue
    return None


def _close(job):
    global _ACTIVE
    process = job.get('process')
    _require(process is None or process.poll() is not None,
             'The controller worker is still running; its owned job was retained: ' + str(job['root']))
    if job.get('log') is not None:
        job['log'].close()
    if _ACTIVE is job:
        _ACTIVE = None
    _JOBS.pop(str(job['root']), None)
    _unwatch_jobs()


def process_running(job):
    process = job.get('process')
    return process is not None and process.poll() is None


def close(job):
    """Release an idle job, never a live child or an Action already handed off."""
    _require(not process_running(job), 'The controller worker is still running; its owner was retained.')
    if job.get('closed'):
        return
    iterator = job.get('continuation')
    if iterator is not None:
        iterator.close()
    recovery = []
    if not job.get('handed_off'):
        try:
            candidate = job.get('candidate')
            if candidate is not None and hasattr(candidate, 'as_pointer'):
                try:
                    if not candidate.as_pointer():
                        candidate = None
                except ReferenceError:
                    # Native load/undo already freed this owned ID. Never try
                    # to recover it by name in the newly loaded artist file.
                    candidate = None
            _remove_output(candidate, job.get('original_actions', set()))
            job['candidate'] = None
        except Exception as exc:
            recovery.append('Action: ' + str(exc))
    if 'append_ids' in job:
        try:
            _remove_append_libraries(job['root'] / 'controller.blend', job['append_ids'])
        except Exception as exc:
            recovery.append('Library stub: ' + str(exc))
    _require(not recovery, 'Controller cleanup needs recovery: ' + '; '.join(recovery))
    _close(job)
    job['closed'] = True
    for key in ('source_capture', 'candidate_capture', 'captures'):
        job.pop(key, None)
    if job.get('cancel_requested'):
        job['state'] = 'CANCELLED'
        try:
            _write(job['root'] / 'cancelled.json', dict(schema=SCHEMA, job=job['root'].name, state='CANCELLED', metrics=job.get('metrics')))
        except OSError as exc:
            job['receipt_error'] = str(exc)


def _stop(job):
    """Never release the owner or its files until this process actually exits."""
    process = job.get('process')
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    _close(job)


def _remove_output(action, original):
    if action is not None and action not in original:
        action.use_fake_user = False
        _require(action.users == 0, 'A rejected controller result gained a user and was retained for recovery.')
        bpy.data.actions.remove(action)


def _remove_append_libraries(path, original_ids):
    """Remove only an authenticated, unused Library stub left by native append."""
    path = Path(path).resolve()
    users = bpy.data.user_map()
    candidates = [item for item in set(users) - original_ids
                  if isinstance(item, bpy.types.Library)
                  and Path(bpy.path.abspath(item.filepath)).resolve() == path]
    _require(len(candidates) <= 1, 'The controller append produced ambiguous Library owners; dependencies were retained.')
    for library in candidates:
        _require(not library.use_fake_user and library.users == int(library.use_extra_user)
                 and library.parent is None and not users.get(library)
                 and not any(getattr(item, 'library', None) == library for item in users)
                 and not any(item.parent == library for item in bpy.data.libraries),
                 'The controller append Library still has references; its dependencies were retained.')
        # Blender retains this empty append stub with one intrinsic extra user.
        # The reference checks above exclude every real dependent ID first.
        bpy.data.libraries.remove(library, do_unlink=True)


def _load_output(job, receipt):
    path = job['root'] / 'controller.blend'
    _require(path.is_file() and _sha(path) == receipt.get('output_sha256'), 'The worker Action library is missing or changed.')
    original = set(bpy.data.actions)
    original_ids = set(bpy.data.user_map())
    selected, action = None, None
    try:
        with bpy.data.libraries.load(str(path), link=False) as (available, loaded):
            _require(available.actions == [receipt['action']], 'The worker library contains an unexpected Action inventory.')
            _require(not available.objects, 'The worker result unexpectedly contains Objects.')
            selected = loaded
            loaded.actions = [receipt['action']]
        action = selected.actions[0]
        _require(action is not None and action not in original and action.library is None,
                 'The worker did not append one fresh local Action.')
        action.use_fake_user = False
        _require(action.users == 0, 'The new worker Action is unexpectedly assigned.')
        _remove_append_libraries(path, original_ids)
        _require(set(bpy.data.user_map()) - original_ids == {action},
                 'The worker appended unexpected dependencies; its files were retained for recovery.')
        slot = next((slot for slot in action.slots if slot.handle == receipt['slot']), None)
        _require(slot is not None and slot.identifier == receipt['slot_identifier'], 'The returned controller slot changed.')
        _require(_action_hash(action, slot.handle) == receipt['action_sha256'], 'The returned controller curves changed.')
        _require(list(map(float, action.frame_range)) == receipt['frame_range'], 'The returned controller frame range changed.')
        return action, slot.handle
    except Exception as exc:
        if action is None and selected is not None:
            action = next((item for item in selected.actions if isinstance(item, bpy.types.Action)), None)
        recovery = []
        for label, cleanup in (
                ('Action', lambda: _remove_output(action, original)),
                ('Library stub', lambda: _remove_append_libraries(path, original_ids))):
            try:
                cleanup()
            except Exception as error:
                recovery.append(label + ': ' + str(error))
        if recovery:
            raise RuntimeError('Controller append cleanup needs recovery: ' + '; '.join(recovery)) from exc
        raise


def _fresh_check_steps(job):
    source, target = job['owners']
    value = yield from _coherent(job, lambda: _fresh_steps(
        bpy.context, source, job['action'], job['slot'], target, captures=job['captures']))
    if value != job['fresh_inputs']:
        raise StaleInputs('The character or source changed while creating controls. Current work and the disk result were retained; prepare again.')
    job['fresh_epoch'] = job['proof_epoch']
    return value


def _final_proofs_steps(job, output, slot, expected_hash):
    """Verify frozen inputs and the candidate in the same stable generation."""
    for _attempt in range(3):
        _phase(job, 'Verifying current inputs before publication')
        yield from _fresh_check_steps(job)
        _phase(job, 'Verifying result channels')
        result_hash = yield from _coherent(job, lambda: _action_hash_steps(output, slot, job['captures']))
        _require(result_hash == expected_hash and _value(dict(output.items())) == job['candidate_metadata'],
                 'The unbound controller result changed during verification.')
        _require(output.users == 0, 'The unbound controller result acquired an unexpected user.')
        if job['fresh_epoch'] == job['proof_epoch'] == job['epoch']:
            return
        yield _TURN
    raise StaleInputs('Inputs kept changing during final verification; the unbound result was refused.')


def _load_output_steps(job, receipt):
    path = job['root'] / 'controller.blend'
    _require(path.is_file() and (yield from _sha_steps(path)) == receipt.get('output_sha256'),
             'The worker Action library is missing or changed.')
    if job['epoch'] != job['fresh_epoch']:
        yield from _fresh_check_steps(job)
    job['metrics']['resources_before_append'] = _resources(job['root'])
    original = set(bpy.data.actions)
    original_ids = set(bpy.data.user_map())
    job['append_ids'] = original_ids
    job['original_actions'] = original
    def append():
        with bpy.data.libraries.load(str(path), link=False) as (available, loaded):
            _require(available.actions == [receipt['action']], 'The worker library contains an unexpected Action inventory.')
            _require(not available.objects, 'The worker result unexpectedly contains Objects.')
            loaded.actions = [receipt['action']]
        action = loaded.actions[0]
        job['candidate'] = action  # Cleanup owns it before the first later validation can fail.
        _require(action is not None and action not in original and action.library is None,
                 'The worker did not append one fresh local Action.')
        action.use_fake_user = False
        _require(action.users == 0, 'The new worker Action is unexpectedly assigned.')
        _remove_append_libraries(path, original_ids)
        _require(set(bpy.data.user_map()) - original_ids == {action},
                 'The worker appended unexpected dependencies; its files were retained for recovery.')
        return action
    action = _atomic(job, 'Appending verified Action', append, appended=True)
    _track(job, action)
    metadata = _value(dict(action.items()))
    yield _TURN
    slot = next((slot for slot in action.slots if slot.handle == receipt['slot']), None)
    _require(slot is not None and slot.identifier == receipt['slot_identifier'], 'The returned controller slot changed.')
    digest = yield from _coherent(job, lambda: _action_hash_steps(action, slot.handle, job['captures']))
    _require(digest == receipt['action_sha256'], 'The returned controller curves changed.')
    _require(list(map(float, action.frame_range)) == receipt['frame_range'], 'The returned controller frame range changed.')
    job['candidate_metadata'] = metadata
    return action, slot.handle


def _conversion(job):
    """Every yielded read runs on Blender's main thread under one short override."""
    from . import animation_controller_mapping as mapping, animation_controller_isolation as isolation
    source, target = job['owners']
    action, slot = job['action'], job['slot']
    root, package = job['root'], Path(__file__).resolve().parent
    _phase(job, 'Checking preparation inputs')
    job['metrics']['resources_before_capture'] = _resources(root)
    for obj in tuple(bpy.data.objects):
        if obj.type == 'ARMATURE':
            _track(job, obj)
            _track(job, obj.data)
        yield
    raw, roots = {}, set()
    def capture():
        raw.clear()
        roots.clear()
        graph = isolation._graph(target)
        yield
        _, secondary = isolation._secondary_sources(target)
        roots.update({job['scene'], source, action, target, *secondary})
        if graph:
            roots.add(graph['collection'])
            roots.update(graph['collection'].objects)
        _require(None not in roots, 'A controller dependency is missing.')
        for item in roots:
            _track(job, item)
        return (yield from _witness_steps(bpy.context, source, action, slot, target, raw_out=raw, captures=job['captures']))
    _phase(job, 'Reading source and character inputs')
    before = yield from _coherent(job, capture)
    job['source_capture'] = job['captures']['values'][(action.as_pointer(), slot)]
    selected = {obj for obj in roots if isinstance(obj, bpy.types.Object) and obj.type == 'ARMATURE'}
    job['raw_owners'] = tuple(selected)
    raw_hashes = {row[0]: row[5] for row in before['rigs']}
    job['fresh_inputs'] = dict(portable=before['portable'], scene=_scene_inputs(job['scene']),
        identities=(job['scene'].as_pointer(), source.as_pointer(), source.data.as_pointer(),
                    action.as_pointer(), target.as_pointer(), target.data.as_pointer()),
        raw={obj.name_full: raw_hashes[obj.as_pointer()] for obj in selected})
    job['fresh_epoch'] = job['proof_epoch']
    package_hashes = {}
    _phase(job, 'Checking worker source files')
    for path in sorted(package.rglob('*.py')):
        package_hashes[path.relative_to(package).as_posix()] = yield from _sha_steps(path)
    request = dict(schema=SCHEMA, job=root.name, parent_pid=os.getpid(), package=str(package),
        parent_identity=_parent_identity(), package_sha256=package_hashes,
        blender_version=list(bpy.app.version), home=job['scene'].name, source=source.name,
        target=target.name, action=action.name, slot=int(slot), source_fps=job['source_fps'],
        daily_edit=job.get('daily_edit', False),
        align_loop_period=job.get('align_loop_period', True),
        align_knees=job.get('align_knees', False),
        align_elbows=job.get('align_elbows', False),
        frame=float(job['scene'].frame_current + job['scene'].frame_subframe),
        scene_inputs=_scene_inputs(job['scene']), input_proof=before['portable'],
        source_proof_format=_SOURCE_COLUMNS if job.get('daily_edit') else 'canonical',
        raw_inputs={obj.name: raw[obj.name_full] for obj in selected},
        object_constraint_switches=_object_constraint_switches(roots))
    raw.clear()
    snapshot = root / 'inputs.blend'
    _phase(job, 'Writing input snapshot (native operation)')
    yield _TURN
    if job['epoch'] != job['proof_epoch']:
        current = yield from _coherent(job, lambda: _witness_steps(bpy.context, source, action, slot, target, captures=job['captures']))
        if not _author_equal(before, current):
            raise StaleInputs('Original inputs changed during preparation. No snapshot was accepted; current work was retained.')
    request['resources_before_snapshot'] = _resources(root)
    _atomic(job, 'Writing input snapshot', lambda: bpy.data.libraries.write(
        str(snapshot), roots, path_remap='ABSOLUTE', fake_user=False, compress=True))
    yield _TURN
    _phase(job, 'Verifying snapshot preservation')
    after = yield from _coherent(job, lambda: _witness_steps(bpy.context, source, action, slot, target, captures=job['captures']))
    _require(_author_equal(before, after), 'Writing the controller snapshot changed original inputs, or inputs were edited during verification; current work and the snapshot were retained.')
    job['fresh_epoch'] = job['proof_epoch']
    _phase(job, 'Checking snapshot file')
    request['snapshot_sha256'] = yield from _sha_steps(snapshot)
    _phase(job, 'Starting background conversion')
    yield _TURN
    if job['epoch'] != job['fresh_epoch']:
        yield from _fresh_check_steps(job)
    request['resources_before_launch'] = _resources(root)
    yield from _write_steps(root / 'job.json', request)
    # Writing the large raw-input request also yields. Keep its snapshot proof
    # frozen if author inputs changed while the JSON was being serialized.
    if job['epoch'] != job['fresh_epoch']:
        yield from _fresh_check_steps(job)
    job['request'] = request
    job['log'] = (root / 'worker.log').open('x', encoding='utf8')
    environment = os.environ.copy()
    environment['BLENDER_USER_CONFIG'] = str(root / 'CONFIG')
    environment['PYTHONUTF8'] = '1'
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    command = [bpy.app.binary_path, '--background', '--factory-startup', '--enable-autoexec',
               '--threads', '2', '--python-exit-code', '1', '--python', str(Path(__file__).resolve()),
               '--', str(root / 'job.json')]
    job['metrics']['resources_at_launch'] = _resources(root)
    job['process'] = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=job['log'],
        stderr=subprocess.STDOUT, env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    job['state'] = 'RUNNING'
    _phase(job, 'Creating controls')
    yield _TURN
    _require(job['process'].poll() is not None, 'The controller worker has not exited.')
    _phase(job, 'Verifying current inputs')
    yield from _fresh_check_steps(job)
    result_path = root / 'result.json'
    receipt = json.loads(result_path.read_text(encoding='utf8')) if result_path.is_file() else {}
    _require(receipt.get('schema') == SCHEMA and receipt.get('job') == root.name,
             'The controller worker did not return a matching receipt. See ' + str(root / 'worker.log'))
    if 'phase_seconds' in receipt:
        job['metrics']['worker_phase_seconds'] = receipt['phase_seconds']
    if receipt.get('state') == 'MAPPING_NEEDED':
        raise mapping.MappingNeeded(receipt.get('error', 'Controller mapping needs review.') + ' See ' + str(root / 'worker.log'))
    _require(job['process'].returncode == 0 and receipt.get('state') == 'READY',
             receipt.get('error', 'Controller worker failed.') + ' See ' + str(root / 'worker.log'))
    _require(receipt.get('input_proof') == request['input_proof'] and receipt.get('snapshot_sha256') == request['snapshot_sha256'],
             'The worker converted a different input snapshot.')
    diagnostics = receipt['diagnostics']
    source_sha = diagnostics.get('source_action_sha256')
    expected_sha = request['input_proof']['source_action']
    if request['source_proof_format'] == _SOURCE_COLUMNS:
        expected_sha = receipt.get('canonical_source_sha256')
        _require(isinstance(expected_sha, str) and len(expected_sha) == 64 and
                 all(c in '0123456789abcdef' for c in expected_sha), 'Missing canonical source Action proof.')
    _require(diagnostics.get('native_output_verified') is True and source_sha == expected_sha,
             'The controller output lost its native verification or source provenance.')
    _phase(job, 'Reading original inputs before append')
    before_append = yield from _coherent(job, lambda: _witness_steps(bpy.context, source, action, slot, target, captures=job['captures']))
    # The same author capture contains the expensive portable proof; do not let a
    # source edit between the preceding fresh check and append pass unnoticed.
    _require(before_append['portable'] == request['input_proof'], 'The source or character changed before result append.')
    _phase(job, 'Appending result (native operation)')
    yield _TURN
    if job['epoch'] != job['proof_epoch']:
        yield from _fresh_check_steps(job)
        before_append = yield from _coherent(job, lambda: _witness_steps(bpy.context, source, action, slot, target, captures=job['captures']))
    output, output_slot = yield from _load_output_steps(job, receipt)
    _phase(job, 'Verifying original inputs after append')
    after_append = yield from _coherent(job, lambda: _witness_steps(bpy.context, source, action, slot, target, captures=job['captures']))
    _require(_author_equal(before_append, after_append),
             'Original inputs changed during result verification; the unbound result was rejected and current work was retained.')
    yield from _final_proofs_steps(job, output, output_slot, receipt['action_sha256'])
    diagnostics['worker'] = dict(job=str(root), snapshot_sha256=request['snapshot_sha256'],
                                 output_sha256=receipt['output_sha256'], parent_evaluation=False)
    job['receipt'] = receipt
    job['candidate_capture'] = job['captures']['values'][(output.as_pointer(), output_slot)]
    job['ready_epoch'] = job['epoch']
    return mapping.MappingResult(output, output_slot, source_sha, diagnostics)


def begin(context, source_rig, source_action, source_slot, target_rig, *, source_fps=None, daily_edit=False,
          align_knees=False, align_elbows=False, align_loop_period=None):
    """Register the pending owner immediately; the first heavy read happens in poll."""
    global _ACTIVE
    from . import animation_controller_mapping as mapping
    if conflicting(source_rig, target_rig) is not None:
        raise RuntimeError('This source or character already has a controller conversion; finish or cancel it first.')
    _require(context.window is not None and any(obj == target_rig for obj in context.scene.objects.values()),
             'Open controller preparation from the character scene.')
    source_fps = mapping._source_fps(source_rig, source_fps)
    if align_loop_period is None:
        worklist = getattr(context.scene, 'character_designer_animation_worklist', None)
        align_loop_period = getattr(worklist, 'align_loop_period', True)
    if (align_knees or align_elbows) and (source_rig != target_rig or daily_edit or (align_knees and align_elbows)):
        raise ValueError('Align one kind of pole on a retained controller Action on its character.')
    parent = Path(bpy.utils.user_resource('DATAFILES', path='character_designer/controller_jobs', create=True)).resolve()
    root = parent / uuid.uuid4().hex
    root.mkdir(exist_ok=False)
    (root / 'CONFIG').mkdir()
    job = dict(root=root, process=None, log=None, owners=(source_rig, target_rig),
        action=source_action, slot=source_slot, source_fps=source_fps, daily_edit=daily_edit,
        align_loop_period=align_loop_period,
        align_knees=align_knees, align_elbows=align_elbows, scene=context.scene,
        layer=context.view_layer, window=context.window, state='PREPARING', phase='Preparing inputs',
        started=time.monotonic(), cancel_requested=False, watched=set(), epoch=0, proof_epoch=0,
        handed_off=False, closed=False, candidate=None,
        metrics=dict(poll_budget_seconds=POLL_BUDGET_SECONDS, capture_retries=0, polls=0,
                     max_poll_seconds=0., max_chunk_seconds=0., phases={}, atomic_operations=[]))
    job['light_inputs'] = _light_inputs(job)
    job['captures'] = dict(values={}, timings=job['metrics']['atomic_operations'])
    if daily_edit:
        job['captures']['source_columns'] = (source_action.as_pointer(), source_slot)
    for value in (source_rig, source_rig.data, source_action, target_rig, target_rig.data):
        _track(job, value)
    job['continuation'] = _conversion(job)
    _JOBS[str(root)] = job
    _ACTIVE = job
    try:
        _watch_jobs()
    except BaseException:
        close(job)
        raise
    return job


def request_cancel(job):
    """Cancel before capture, between slices, or during the owned child; never wait."""
    if job['cancel_requested'] or job.get('closed'):
        return
    job['cancel_requested'] = True
    job['phase'] = 'Cancelling'
    job['cancel_started'] = time.monotonic()
    job['kill_requested'] = False
    if process_running(job):
        job['process'].terminate()
    try:
        _write(job['root'] / 'cancel.json', dict(schema=SCHEMA, job=job['root'].name, state='CANCELLING'))
    except OSError as exc:
        job['receipt_error'] = str(exc)


def _advance(job):
    started = time.monotonic()
    metrics = job['metrics']
    metrics['polls'] += 1
    try:
        with bpy.context.temp_override(window=job['window'], scene=job['scene'], view_layer=job['layer']):
            # A timer slice owns the main thread until it returns. Re-read the
            # cheap guard once per slice, not once per JSON/RNA scalar; coherent
            # captures and native-operation boundaries retain their own checks.
            _guard(job)
            for _step in range(_MAX_POLL_STEPS):
                phase = job['phase']
                chunk_start = time.monotonic()
                try:
                    value = next(job['continuation'])
                except StopIteration as done:
                    job['result'] = done.value
                    job['state'] = 'READY'
                    job['phase'] = 'Controllers ready'
                    return dict(state='EXITED')
                finally:
                    elapsed = time.monotonic() - chunk_start
                    metrics['max_chunk_seconds'] = max(metrics['max_chunk_seconds'], elapsed)
                    metrics['phases'][phase] = metrics['phases'].get(phase, 0.) + elapsed
                if value is _TURN or job['state'] == 'RUNNING' or time.monotonic() - started >= POLL_BUDGET_SECONDS:
                    return None
    finally:
        metrics['max_poll_seconds'] = max(metrics['max_poll_seconds'], time.monotonic() - started)
    return None


def poll(job):
    """Advance bounded main-thread reads; native library I/O is explicitly measured."""
    if job.get('closed'):
        return dict(state='CANCELLED' if job['cancel_requested'] else 'EXITED')
    if job['cancel_requested']:
        if process_running(job):
            if time.monotonic() - job['cancel_started'] >= 5 and not job['kill_requested']:
                job['kill_requested'] = True
                job['process'].kill()
            return None
        close(job)
        return dict(state='CANCELLED')
    try:
        if job.get('state') == 'READY':
            return dict(state='EXITED')
        if job.get('state') in {'PREPARING', 'VERIFYING'}:
            return _advance(job)
        process = job['process']
        code = process.poll()
        now = time.monotonic()
        if code is None and 'last_status_poll' in job and now - job['last_status_poll'] < 1.:
            return None
        job['last_status_poll'] = now
        progress = job['root'] / 'progress.json'
        if progress.is_file():
            try:
                value = json.loads(progress.read_text(encoding='utf8'))
                if isinstance(value, dict) and value.get('job') == job['root'].name:
                    job['phase'] = str(value.get('phase') or 'Creating controls')
                    job['progress_detail'] = {k: value[k] for k in ('sample', 'total', 'round', 'updated', 'heartbeat')
                                              if type(value.get(k)) in (int, float) and math.isfinite(value[k])}
            except (OSError, ValueError):
                pass
        published = _write(job['root'] / 'process.json', dict(schema=SCHEMA, job=job['root'].name, parent_pid=os.getpid(),
            worker_pid=process.pid, elapsed=now - job['started'], running=code is None, returncode=code), heartbeat=True)
        if code is None:
            return None
        if published is False and not job.get('retried_exit_heartbeat'):
            # Try the terminal heartbeat again on the next tick. A diagnostic
            # reader cannot hold up mandatory result verification indefinitely.
            job['retried_exit_heartbeat'] = True
            return None
        # Older script-created transactions retain their original finish API.
        if 'metrics' not in job:
            return dict(state='EXITED')
        job.pop('progress_detail', None)
        job['state'] = 'VERIFYING'
        job['phase'] = 'Verifying result'
        return None
    except BaseException as exc:
        job['state'] = 'ERROR'
        try:
            if not process_running(job):
                close(job)
        finally:
            _write(job['root'] / 'parent_error.json', dict(schema=SCHEMA, job=job['root'].name,
                error=str(exc), type=type(exc).__name__, traceback=traceback.format_exc(), metrics=job.get('metrics')))
        raise


def finish(job):
    """A small handoff after poll finished all expensive checks and readback."""
    _require(not job['cancel_requested'], 'Cancellation wins over the completed worker result.')
    _require(not process_running(job), 'The controller worker has not exited.')
    if 'metrics' not in job:
        try:
            next(job['continuation'])
        except StopIteration as result:
            return result.value
        raise RuntimeError('The controller worker continuation yielded unexpectedly.')
    _require(job['state'] == 'READY' and not job.get('closed'), 'The controller result is not ready for handoff.')
    from . import animation_controller_mapping as mapping
    with bpy.context.temp_override(window=job['window'], scene=job['scene'], view_layer=job['layer']):
        _guard(job)
        late_update = job['epoch'] != job['ready_epoch']
        _atomic(job, 'Publication raw-input check', lambda: _publication_raw(job))
        result = job['result']
        _require(result.action.users == 0, 'The controller result acquired an unexpected user before publication.')
        _atomic(job, 'Publication source Action check', lambda: _publication_action(
            job, job['action'], job['slot'], job['source_capture'], 'Source Action'))
        _atomic(job, 'Publication candidate Action check', lambda: _publication_action(
            job, result.action, result.slot, job['candidate_capture'], 'Controller result'))
        if _digest(_value(dict(job['action'].items()))) != job['fresh_inputs']['portable']['source_metadata']:
            raise StaleInputs('Source Action metadata changed before publication. Current work was retained.')
        _require(_value(dict(result.action.items())) == job['candidate_metadata'],
                 'The unbound controller result metadata changed before publication.')
        if late_update:
            _atomic(job, 'Publication full-input recheck after native notification', lambda: _publication_fresh(job))
            job['metrics']['late_notification_rechecks'] = job['metrics'].get('late_notification_rechecks', 0) + 1
        result.action[mapping.RECORD] = _json(result.diagnostics)
    snapshot = job['root'] / 'inputs.blend'
    cleanup = ''
    try:
        _require(not any(Path(bpy.path.abspath(lib.filepath)).resolve() == snapshot for lib in bpy.data.libraries),
                 'The controller snapshot acquired a library user.')
        snapshot.unlink()
    except (OSError, RuntimeError) as exc:
        cleanup = str(exc)
    # Cleanup may fail (for example an unexpected retained Library user). Keep
    # explicit ownership of the unbound Action until cleanup actually succeeds;
    # the service has no READY record yet and must not lose this recovery owner.
    candidate = job.pop('candidate', None)
    try:
        close(job)
    except BaseException:
        job['candidate'] = candidate
        raise
    job['handed_off'] = True
    try:
        _write(job['root'] / 'accepted.json', dict(schema=SCHEMA, job=job['root'].name, action=result.action.name,
            slot=result.slot, action_sha256=job['receipt']['action_sha256'],
            input_snapshot_removed=not snapshot.exists(), cleanup_warning=cleanup, metrics=job['metrics']))
    except OSError as exc:
        # The verified Action is ready for the caller even if its optional disk
        # receipt cannot be written. Do not lose an accepted local result.
        job['receipt_error'] = str(exc)
        result.diagnostics.setdefault('worker', {})['receipt_error'] = str(exc)
        result.action[mapping.RECORD] = _json(result.diagnostics)
    return result


def build(context, source_rig, source_action, source_slot, target_rig, *, source_fps=None):
    """Synchronous scripts use the same preparation, verification and ownership path."""
    job = begin(context, source_rig, source_action, source_slot, target_rig, source_fps=source_fps)
    try:
        while poll(job) is None:
            time.sleep(.001 if job['state'] in {'PREPARING', 'VERIFYING'} else .1)
        return finish(job)
    except BaseException:
        _stop(job)
        close(job)
        raise


def _child(job_path):
    """Standalone entry: never called by the artist-side service."""
    path = Path(job_path).resolve(strict=True)
    root = path.parent
    request = json.loads(path.read_text(encoding='utf8'))
    _require(bpy.app.background and request.get('schema') == SCHEMA and request.get('job') == root.name
             and request.get('parent_pid') != os.getpid(), 'Controller worker ownership was not established.')
    _require(Path(os.environ.get('BLENDER_USER_CONFIG', '')).resolve() == root / 'CONFIG',
             'Controller worker needs its private CONFIG.')
    receipt = dict(schema=SCHEMA, job=root.name, state='ERROR')
    stop_parent_watch = None
    progress = _ChildProgress(root)
    try:
        stop_parent_watch = _watch_parent(request, root, heartbeat=progress.heartbeat)
        progress.report('Loading snapshot')
        package = Path(request['package']).resolve(strict=True)
        sys.dont_write_bytecode = True
        _require(_package_hashes(package) == request['package_sha256'], 'The controller worker package changed.')
        _require(list(bpy.app.version) == request['blender_version'], 'The controller worker Blender version differs.')
        sys.path.insert(0, str(package.parent))
        addon = importlib.import_module(package.name)
        _require(Path(addon.__file__).resolve().parent == package, 'The worker imported another add-on package.')
        addon.register()
        mapping = importlib.import_module(package.name + '.animation_controller_mapping')
        helpers = importlib.import_module(package.name + '.animation_controller_worker')
        snapshot = root / 'inputs.blend'
        _require(_sha(snapshot) == request['snapshot_sha256'], 'The controller input snapshot changed.')
        _require(bpy.ops.wm.open_mainfile(filepath=str(snapshot), use_scripts=True) == {'FINISHED'},
                 'The controller snapshot did not open.')
        home = bpy.data.scenes.get(request['home'])
        source = bpy.data.objects.get(request['source'])
        target = bpy.data.objects.get(request['target'])
        action = bpy.data.actions.get(request['action'])
        _require(home is not None and source is not None and target is not None and action is not None,
                 'The controller snapshot lost its scene, character, source or Action.')
        bpy.context.window.scene = home
        _require(helpers._scene_inputs(home) == request['scene_inputs'], 'The snapshot scene timing or units changed.')
        natural = importlib.import_module(package.name + '.natural_bind_mode')
        pending = bpy.app.timers.is_registered(natural._after_read)
        lifecycle = dict(pending_before=pending, loaded_inputs_before=sorted(natural._loaded_inputs))
        receipt['load_lifecycle'] = lifecycle
        if pending:
            # Background Python does not drive the normal deferred load timer.
            # Consume exactly that scheduled callback before seeding RAM inputs.
            bpy.app.timers.unregister(natural._after_read)
            natural._after_read()
        lifecycle.update(pending_after=bpy.app.timers.is_registered(natural._after_read),
                         loaded_inputs_after=sorted(natural._loaded_inputs))
        # Seed immediate RAM inputs after native file loading, only in this worker.
        for name, raw in request['raw_inputs'].items():
            rig = bpy.data.objects.get(name)
            _require(rig is not None and rig.type == 'ARMATURE', 'The snapshot lost an authenticated Armature.')
            helpers._restore_packet(rig, raw)
        helpers._restore_object_constraint_switches(request.get('object_constraint_switches', ()))
        action_hash = helpers.make_action_hasher()
        fmt = request.get('source_proof_format', 'canonical')
        _require(fmt in ('canonical', _SOURCE_COLUMNS), 'Unsupported controller input proof format.')
        portable_hash = (helpers.make_action_hasher(source_columns=(action.as_pointer(), request['slot']))
                         if fmt == _SOURCE_COLUMNS else action_hash)
        proof = helpers._portable(source, action, request['slot'], target, action_hash=portable_hash)
        changed = [key for key in request['input_proof'] if proof.get(key) != request['input_proof'][key]]
        _require(proof == request['input_proof'], 'The controller snapshot differs from current RAM: ' + ', '.join(changed))
        progress.report('Preparing bone mapping')
        daily_edit = request.get('daily_edit', False)
        _require(type(daily_edit) is bool, 'Invalid imported motion accuracy in the worker request.')
        align_loop_period = request.get('align_loop_period', True)
        _require(type(align_loop_period) is bool, 'Invalid loop timing option in the worker request.')
        align_knees = request.get('align_knees', False)
        align_elbows = request.get('align_elbows', False)
        _require(type(align_knees) is bool and type(align_elbows) is bool and not (align_knees and align_elbows),
                 'Unsupported pole alignment request.')
        if align_knees or align_elbows:
            _require(source == target and not daily_edit, 'Pole alignment lost its retained character.')
            module = '.animation_elbow_pole_repair' if align_elbows else '.animation_knee_pole_repair'
            repair = importlib.import_module(package.name + module)
            result = repair.repair_action(bpy.context, target, action, request['slot'],
                                          proof['source_action'], progress.report)
        elif source == target:
            variants = importlib.import_module(package.name + '.animation_controller_variant')
            result = variants.from_fk(bpy.context, target, action, request['slot'],
                                      proof['source_action'], progress.report)
        else:
            result = mapping._build_isolated(bpy.context, source, action, request['slot'], target,
                                             source_fps=request['source_fps'], progress=progress.report,
                                             daily_edit=daily_edit, action_hash=action_hash,
                                             align_loop_period=align_loop_period)
        _require(result.action.users == 0, 'The worker output Action is still assigned.')
        slot = next((s for s in result.action.slots if s.handle == result.slot), None)
        _require(slot is not None, 'The controller output lost its slot.')
        if not (align_knees or align_elbows):
            cleanup = importlib.import_module(package.name + '.animation_cleanup')
            progress.report('Cleaning prepared clip')
            receipt['cleanup'] = cleanup.clean(target, result.action, result.slot)
        output = root / 'controller.blend'
        progress.report('Writing verified Action')
        bpy.data.libraries.write(str(output), {result.action}, path_remap='ABSOLUTE', fake_user=True, compress=True)
        receipt.update(state='READY', input_proof=proof, snapshot_sha256=request['snapshot_sha256'],
                       canonical_source_sha256=result.source_fingerprint,
                       action=result.action.name, slot=result.slot, slot_identifier=slot.identifier,
                       action_sha256=action_hash(result.action, result.slot),
                       frame_range=list(map(float, result.action.frame_range)),
                       output_sha256=_sha(output), diagnostics=result.diagnostics)
        _require(_package_hashes(package) == request['package_sha256'],
                 'The controller worker package changed during conversion; its result was not accepted.')
        receipt['phase_seconds'] = progress.timings()
        _write(root / 'result.json', receipt)
    except BaseException as exc:
        mapping_module = sys.modules.get(Path(request.get('package', '')).name + '.animation_controller_mapping')
        needed = mapping_module is not None and isinstance(exc, mapping_module.MappingNeeded)
        receipt.update(state='MAPPING_NEEDED' if needed else 'ERROR', error=str(exc),
                       type=type(exc).__name__, traceback=traceback.format_exc(), phase_seconds=progress.timings())
        _write(root / 'result.json', receipt)
        raise
    finally:
        if stop_parent_watch is not None:
            stop_parent_watch()


if __name__ == '__main__':
    _child(sys.argv[sys.argv.index('--') + 1])
