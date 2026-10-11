"""Tiny disposable native receipt for Wiggle 2 dynamic RNA storage.

Accepts the same --addons-root / --wiggle-file / --report-json arguments as
test_skirt_wiggle_blender.py. It does not open an artist file or run physics.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_skirt_wiggle_blender import argument, real_backend, motion
import bpy

backend, owned = real_backend()
bpy.ops.wm.read_factory_settings(use_empty=True)
data = bpy.data.armatures.new('Disposable RNA Probe')
rig = bpy.data.objects.new('Disposable RNA Probe', data)
bpy.context.scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
bone = data.edit_bones.new('Probe')
bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
bpy.ops.object.mode_set(mode='POSE')
bone = rig.pose.bones['Probe']
callback = backend.update_prop
receipt = {'blender': bpy.app.version_string, 'backend_sha256': motion.BACKEND_SHA256}


def values():
    return {'tail': bone.wiggle_tail, 'tail_set': bone.is_property_set('wiggle_tail'),
            'damp': bone.wiggle_damp, 'damp_set': bone.is_property_set('wiggle_damp'),
            'id_tail': bone.get('wiggle_tail'), 'id_damp': bone.get('wiggle_damp')}


receipt['initial'] = values()
bone['wiggle_tail'], bone['wiggle_damp'] = True, 7.0
receipt['id_assignment'] = values()
with motion._native_writes(backend):
    motion._native_set(bone, 'wiggle_tail', True)
    motion._native_set(bone, 'wiggle_damp', 7.0)
receipt['rna_assignment'] = values()
with motion._native_writes(backend):
    bone.property_unset('wiggle_tail')
    bone.property_unset('wiggle_damp')
receipt['rna_unset'] = values()
try:
    with motion._native_writes(backend):
        raise RuntimeError('scoped setter failure')
except RuntimeError:
    pass
receipt['callback_restored_on_success_and_exception'] = backend.update_prop is callback
receipt['passed'] = (receipt['rna_assignment']['tail'] is True
                     and receipt['rna_assignment']['damp'] == 7.0
                     and receipt['rna_unset']['tail'] is False
                     and receipt['callback_restored_on_success_and_exception'])
if owned:
    backend.unregister()
if argument('--report-json'):
    Path(argument('--report-json')).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
print(json.dumps(receipt, indent=2))
if not receipt['passed']:
    raise SystemExit(1)
