"""Native Blender count and displayed-scope regression for the Key Reduction preview."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

package, result = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, package)
import character_designer
from character_designer import animation_preview_session as preview, animation_preview_ui as ui
from character_designer.animation_preview_curves import signature

character_designer.register()
ctx = bpy.context
scene = ctx.scene
armature = bpy.data.armatures.new('Reduction Counts Skeleton')
rig = bpy.data.objects.new('Reduction Counts', armature)
scene.collection.objects.link(rig)
ctx.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
for index, name in enumerate(('Moving "IK"', 'Protected IK', 'Excluded')):
    bone = armature.edit_bones.new(name)
    bone.head, bone.tail = (index, 0, 0), (index, 0, 1)
bpy.ops.object.mode_set(mode='OBJECT')
moving, protected, excluded = list(rig.pose.bones)
for frame in range(1, 10):
    for bone in rig.pose.bones:
        bone.location.x = frame * .001
        bone.keyframe_insert('location', frame=frame)
    rig['Control State'] = float(frame)
    rig.keyframe_insert('["Control State"]', frame=frame)

ad = rig.animation_data
original, handle = ad.action, ad.action_slot_handle
strip = original.layers[0].strips[0]
bag = strip.channelbag(ad.action_slot)
protected_path = protected.path_from_id('location')
for curve in bag.fcurves:
    for point in curve.keyframe_points:
        point.interpolation = 'LINEAR'
    if curve.data_path == protected_path and curve.array_index == 0:
        curve.lock = True
other = original.slots.new('OBJECT', 'Other object')
other_curves = strip.channelbag(other, ensure=True).fcurves
other_curve = other_curves.new(moving.path_from_id('location'), index=0)
other_curve.keyframe_points.insert(1, 125)
other_curve.keyframe_points.insert(9, 250)
assert ad.action_slot_handle == handle

bpy.ops.object.mode_set(mode='POSE')
for bone in rig.pose.bones:
    if 'select' in bone.bl_rna.properties:
        bone.select = bone != excluded
    else:
        bone.bone.select = bone != excluded
scene.frame_set(4)
original_hashes = signature(original, handle), signature(original, other.handle)
op = bpy.ops.character_designer.key_reduction_preview
assert op(operation='START', position_mm=10000, rotation_degrees=100) == {'FINISHED'}
record = rig[preview.SESSION]
clone = record['preview']

# A preview already open in an older release stores before/after but no breakdown.
settings = json.loads(record['settings'])
settings['before'], settings['after'] = 1000, 1001
record['settings'] = json.dumps(settings)
properties = ('before', 'after', 'reduced_before', 'reduced_after', 'removed', 'same_count', 'added')

def stats():
    shown = preview.status(ctx)
    return {key: shown[key] for key in properties}

daily = stats()
assert tuple(daily.values()) == (54, 33, 27, 6, 21, 27, 0), daily
assert signature(clone, other.handle) == original_hashes[1]

labels = []

class Layout:
    def row(self, **_):
        return Layout()

    box = row

    def label(self, *, text, **_):
        labels.append(text)

    def prop(self, *_args, **_kw):
        pass

    def operator(self, *_args, **_kw):
        return SimpleNamespace()

ui.draw(Layout(), ctx, preview.status(ctx))
assert labels == ['Key Reduction', '27 → 6  (−77.8%)'], labels
compact_labels = list(labels)
labels.clear()
ui.CHARACTERDESIGNER_OT_key_reduction_preview.draw(SimpleNamespace(layout=Layout()), ctx)
assert 'Same key count  27' in labels and 'Selected total  54 → 33' in labels, labels

reduced_bag = clone.layers[0].strips[0].channelbag(ad.action_slot)
new_curve = reduced_bag.fcurves.new(moving.path_from_id('rotation_euler'), index=0)
new_curve.keyframe_points.insert(1, .125)
new_curve.keyframe_points.insert(9, .5)
victim = next(c for c in reduced_bag.fcurves if c.data_path == protected_path and c.array_index == 2)
reduced_bag.fcurves.remove(victim)
changed = stats()
assert tuple(changed.values()) == (54, 26, 36, 6, 30, 18, 2), changed
assert changed['before'] - changed['after'] == changed['removed'] - changed['added']
assert op(operation='ORIGINAL') == {'FINISHED'}
assert stats() == changed
assert op(operation='REDUCED') == {'FINISHED'}
assert stats() == changed
assert op(operation='DISCARD') == {'FINISHED'}
assert signature(original, handle) == original_hashes[0] and signature(original, other.handle) == original_hashes[1]
Path(result).write_text(json.dumps(dict(version=character_designer.bl_info['version'], automatic=daily,
    edited=changed, compact_labels=compact_labels, detail_labels=labels, original_preserved=True), ensure_ascii=False, indent=2), encoding='utf8')
print('PASS native selected slot counts, saved settings compatibility, edited preview and rendered label text.')
