"""Read the saved character in isolation; never save or alter the original file."""
import hashlib
import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_workflow_blender import character_designer, C, bank, work, ui, layout

character_designer.register()
path = Path(sys.argv[sys.argv.index('--')+1])
before = hashlib.sha256(path.read_bytes()).hexdigest()
bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
obj = next(o for o in bpy.data.objects if o.type == 'MESH' and len(o.character_designer_finger_bank.slots))
if C.object and C.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
for selected in C.selected_objects: selected.select_set(False)
obj.select_set(True); C.view_layer.objects.active = obj
bpy.ops.object.mode_set(mode='EDIT')
ui.hide()
baseline = layout.fingerprint(obj)
work.release(C)  # Only discard stale preparation snapshots in this disposable process.
report = {}
for digit in bank.detect.DIGITS:
    bank.select(C, digit, 'L')
    try:
        pair = work.prepare(C)
        record = json.loads(pair.automatic_defaults)
        record['current'] = [j.position for j in pair.joints]
        try:
            work.restore_defaults(C, automatic=True)
            record['safe_positions'] = True
        except ValueError as exc: record['reason'] = str(exc)
        report[digit] = record
    except (ValueError, RuntimeError) as exc: report[digit] = {'error': str(exc)}
assert baseline == layout.fingerprint(obj)
assert before == hashlib.sha256(path.read_bytes()).hexdigest()
print('REAL_FINGER_DEFAULTS', json.dumps(report), flush=True)
print('SOURCE_MESH_AND_FILE_UNCHANGED', flush=True)
