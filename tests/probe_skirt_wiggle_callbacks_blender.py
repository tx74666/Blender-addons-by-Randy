"""Locate upstream callback errors without suppressing the original callback."""
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_skirt_wiggle_blender import argument, real_backend, fixture, motion
import bpy

backend, owned = real_backend()
original = backend.update_prop
phase = 'setup'
observed = {}
stacks = []


def observe(owner, context, field):
    if isinstance(owner, bpy.types.PoseBone) and context.selected_pose_bones is None:
        row = observed.setdefault(phase, {'count': 0, 'fields': []})
        row['count'] += 1
        if field not in row['fields']:
            row['fields'].append(field)
        if len(stacks) < 3:
            stacks.append({'phase': phase, 'field': field, 'stack': traceback.format_stack()})
    return original(owner, context, field)


backend.update_prop = observe
try:
    phase = 'fixture_shared_build'
    print('CALLBACK_PROBE_PHASE', phase, flush=True)
    sources, rig = fixture(shared=True)
    phase = 'owned_automatic_switch'
    print('CALLBACK_PROBE_PHASE', phase, flush=True)
    motion.set_mode(bpy.context, sources[0], 'AUTOMATIC')
    phase = 'owned_manual_switch'
    print('CALLBACK_PROBE_PHASE', phase, flush=True)
    motion.set_mode(bpy.context, sources[0], 'MANUAL')
    phase = 'fixture_separate_build'
    print('CALLBACK_PROBE_PHASE', phase, flush=True)
    fixture(shared=False)
finally:
    motion.unregister()
    backend.update_prop = original
    if owned:
        backend.unregister()
receipt = {'blender': bpy.app.version_string, 'phase_callbacks': observed,
           'first_callback_stacks': stacks, 'callback_restored': backend.update_prop is original}
if argument('--report-json'):
    Path(argument('--report-json')).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
print(json.dumps(receipt, indent=2))
