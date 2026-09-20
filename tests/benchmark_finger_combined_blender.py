"""Combined Start/End and joint overlays, actual update notifications, no saves."""
import cProfile
import hashlib
import json
import pstats
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

import bpy

args = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
profile = '--profile' in args
if profile: args.remove('--profile')
all_digits = '--all' in args
if all_digits: args.remove('--all')
guides_first = '--guides-first' in args
if guides_first: args.remove('--guides-first')
if '--baseline' in args:
    args.remove('--baseline')
    sys.path.insert(0, str(Path(bpy.utils.user_resource('SCRIPTS')) / 'addons'))
    import character_designer
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_workflow_blender import bound_fixture, character_designer, C, bank, work, ui, layout
from character_designer import finger_definition_ui as guides, finger_definition as definition

character_designer.register()
path = Path(args[0]) if args else None
file_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
if path:
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    obj = next(o for o in bpy.data.objects if o.type == 'MESH' and len(o.character_designer_finger_bank.slots))
    if C.object and C.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
    for selected in C.selected_objects: selected.select_set(False)
    obj.select_set(True); C.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bank.select(C, 'INDEX', 'L')
    ui.hide(); work.release(C)
else: obj, _, _ = bound_fixture()
if all_digits: obj.character_designer_finger_bank.visible_digits = set(bank.detect.DIGITS)
baseline = layout.fingerprint(obj)
pair = work.prepare(C)
ui.show(C); guides.show(invalidate=False)
guides.display_frames(C); guides.cached_frame(C)
initial = pair.joints[0].position
timings, frames, idle = [], [], []
profiler = cProfile.Profile()
with patch.object(definition, 'frame', wraps=definition.frame) as checks:
    if profile: profiler.enable()
    for i in range(10):
        start = time.perf_counter()
        pair.joints[0].position = initial + .001*(i % 5)
        C.view_layer.update()
        if guides_first:
            guides.cached_frame(C); guides.display_frames(C)
        ui._refresh()
        data = guides.display_frames(C); guides.cached_frame(C)
        timings.append((time.perf_counter()-start)*1000)
        assert len(data) == (10 if all_digits else 2)
        assert ui.joint_preview_visible(C)
        frames.append((ui._preview['groups'], [tuple(tuple(p) for p in d['path']) for d in data]))
    drag_checks = checks.call_count
    for i in range(10):
        start = time.perf_counter()
        C.view_layer.update(); ui._refresh()
        guides.display_frames(C); guides.cached_frame(C)
        idle.append((time.perf_counter()-start)*1000)
    if profile:
        profiler.disable(); pstats.Stats(profiler).strip_dirs().sort_stats('cumulative').print_stats(30)
    total_checks = checks.call_count
assert layout.fingerprint(obj) == baseline
if path: assert hashlib.sha256(path.read_bytes()).hexdigest() == file_hash
print('COMBINED_PREVIEW', json.dumps(dict(
    version=character_designer.bl_info['version'], all_digits=all_digits,
    guides_first=guides_first, vertices=len(obj.data.vertices), file_hash=file_hash,
    drag_median_ms=statistics.median(timings), drag_min_ms=min(timings), drag_max_ms=max(timings),
    idle_median_ms=statistics.median(idle), reference_rechecks=drag_checks, idle_rechecks=total_checks-drag_checks,
    frame_hash=hashlib.sha256(repr(frames).encode()).hexdigest(), unchanged=True)), flush=True)
