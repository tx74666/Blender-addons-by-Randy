"""Blender background benchmark; optional -- <model.blend>. Never saves model.

Includes SELECT operator, frame assembly, active-panel validation and header
draw preparation. This is CPU work, not OS input latency or display presentation.
"""
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import bound_fixture, character_designer, C, bank, definition
from character_designer import finger_definition_ui as ui, finger_bank_ui as bank_ui


class Panel:
    def row(self, **kwargs): return self
    def column(self, **kwargs): return self
    def label(self, **kwargs): pass
    def operator(self, *args, **kwargs): return SimpleNamespace()


def measure(digit, mode='SINGLE'):
    start = time.perf_counter()
    bpy.ops.character_designer.finger_setup(action='SELECT', digit=digit, selection_mode=mode)
    frames = ui.display_frames(C)
    ui.cached_frame(C)
    bank_ui.draw_header(Panel(), C)
    return (time.perf_counter()-start)*1000, len(frames)


character_designer.register()
args = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
if args:
    bpy.ops.wm.open_mainfile(filepath=str(Path(args[0]).resolve()), load_ui=False)
    obj = bank.active_object(C)
    if obj is None:
        obj = next(o for o in bpy.data.objects if o.type == 'MESH' and len(o.character_designer_finger_bank.slots))
    if C.object and C.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
    for selected in C.selected_objects: selected.select_set(False)
    obj.select_set(True); C.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
else: obj, _, _ = bound_fixture()
C.view_layer.update()
ui.redraw()
result = {'model': args[0] if args else 'synthetic', 'vertices': len(obj.data.vertices),
          'version': character_designer.bl_info['version']}
result['cold_pair_ms'], axes = measure('INDEX')
assert axes == 1
result['warm_pair_ms'], _ = measure('INDEX')
measure('THUMB')
result['remaining_cold_pairs_ms'], axes = measure('PINKY', 'RANGE')
assert axes == 5
old = definition.frame, definition._snapshot, bank.dirty
def forbidden(*args, **kwargs): raise AssertionError('Warm preview scanned geometry')
definition.frame = definition._snapshot = bank.dirty = forbidden
try:
    timings = [measure(bank.detect.DIGITS[i % 5])[0] for i in range(100)]
    result['100_warm_pair_switches_ms'] = {'median': statistics.median(timings), 'max': max(timings), 'min': min(timings)}
    measure('THUMB')
    result['warm_five_pairs_ms'], axes = measure('PINKY', 'RANGE')
    assert axes == 5
    bpy.ops.character_designer.finger_setup(action='TOGGLE')
    bpy.ops.character_designer.finger_setup(action='TOGGLE')
    assert len(ui.display_frames(C)) == 5
    result['no_warm_revalidation'] = True
finally:
    definition.frame, definition._snapshot, bank.dirty = old
print('FINGER_PREVIEW_BENCHMARK', json.dumps(result), flush=True)
