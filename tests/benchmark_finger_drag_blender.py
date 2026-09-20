"""Compare joint drag CPU cost; optional -- <model.blend> [--baseline]. No saves."""
import hashlib
import json
import statistics
import sys
import time
import cProfile
import pstats
from pathlib import Path

import bpy

args = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
profile = '--profile' in args
if profile: args.remove('--profile')
if '--baseline' in args:
    args.remove('--baseline')
    sys.path.insert(0, str(Path(bpy.utils.user_resource('SCRIPTS')) / 'addons'))
    import character_designer  # Keep installed baseline loaded before test imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_workflow_blender import bound_fixture, character_designer, C, bank, work, ui, layout

character_designer.register()
path = Path(args[0]) if args else None
before_file = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
if path:
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    obj = bank.active_object(C)
    if obj is None:
        obj = next(o for o in bpy.data.objects if o.type == 'MESH' and len(o.character_designer_finger_bank.slots))
    if C.object and C.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
    for selected in C.selected_objects: selected.select_set(False)
    obj.select_set(True); C.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bank.select(C, 'INDEX', 'L')
    # Discard only saved preparation metadata in this disposable process.
    work.release(C)
else: obj, _, _ = bound_fixture()
baseline = layout.fingerprint(obj)
pair = work.prepare(C)
ui.show(C)
initial = pair.joints[0].position
timings, frames = [], []
profiler = cProfile.Profile()
if profile: profiler.enable()
for i in range(30):
    start = time.perf_counter()
    pair.joints[0].position = initial + .001*(i % 10)
    C.view_layer.update()
    ui._refresh()
    timings.append((time.perf_counter()-start)*1000)
    assert ui.joint_preview_visible(C)
    frames.append(ui._preview['groups'])
if profile:
    profiler.disable(); pstats.Stats(profiler).strip_dirs().sort_stats('cumulative').print_stats(25)
assert layout.fingerprint(obj) == baseline
if path: assert hashlib.sha256(path.read_bytes()).hexdigest() == before_file
print('FINGER_DRAG_BENCHMARK', json.dumps({
    'model': str(path) if path else 'synthetic', 'version': character_designer.bl_info['version'],
    'vertices': len(obj.data.vertices), 'shape_keys': len(obj.data.shape_keys.key_blocks) if obj.data.shape_keys else 0,
    'median_ms': statistics.median(timings), 'min_ms': min(timings), 'max_ms': max(timings),
    'frame_hash': hashlib.sha256(repr(frames).encode()).hexdigest(),
    'unchanged_mesh_and_file': True}), flush=True)
