"""Reproduce a 565-key preview with Repeat Motion on only some XYZ axes."""
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
from mathutils import Euler

package, output, expected = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, package)
import character_designer
from character_designer import animation_preview_curves as curves
from character_designer import animation_preview_session as preview
from character_designer import animation_preview_ui as ui
from character_designer.animation_local_motion_pins import assign_character_action

character_designer.register()
context = bpy.context
scene = context.scene
rig = bpy.data.objects.new('Mixed Cycles Rig', bpy.data.armatures.new('Mixed Cycles Armature'))
scene.collection.objects.link(rig)
context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
bone = rig.data.edit_bones.new('Hand IK')
bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
bpy.ops.object.mode_set(mode='POSE')
hand = rig.pose.bones['Hand IK']
hand.rotation_mode = 'XYZ'
if 'select' in hand.bl_rna.properties:
    hand.select = True
else:
    hand.bone.select = True
hand['ik_fk'] = 1.

action = bpy.data.actions.new('Mixed Cycles Action')
action.pose_markers.new('Contact').frame = 42
slot = action.slots.new('OBJECT', rig.name)
other_slot = action.slots.new('OBJECT', 'Other rig')
strip = action.layers.new('Motion').strips.new(type='KEYFRAME')
bag = strip.channelbag(slot, ensure=True)
other = strip.channelbag(other_slot, ensure=True)
location = hand.path_from_id('location')
rotation = hand.path_from_id('rotation_euler')
scale = hand.path_from_id('scale')
switch = hand.path_from_id('["ik_fk"]')


def write(path, axis, frames, value, *, cyclic=False, extrapolation='CONSTANT'):
    curve = bag.fcurves.new(path, index=axis)
    curve.extrapolation = extrapolation
    for frame in frames:
        point = curve.keyframe_points.insert(frame, value(frame))
        point.interpolation = 'LINEAR'
        if frame == 51:
            point.type = 'BREAKDOWN'
    if cyclic:
        modifier = curve.modifiers.new('CYCLES')
        assert modifier.mode_before == modifier.mode_after == 'REPEAT'
    return curve


for axis in range(3):
    write(location, axis, range(1, 94),
          lambda t: .07 * math.sin(t / (8 + axis) + axis),
          cyclic=axis == 0, extrapolation='LINEAR' if axis == 1 else 'CONSTANT')
    write(rotation, axis, range(axis + 1, axis + 94),
          lambda t: .3 * math.sin(t / (8 + axis) + axis), cyclic=axis != 1)
    write(scale, axis, (1, 95), lambda t: 1.)
write(switch, 0, (1,), lambda t: 1.)
other.fcurves.new(location).keyframe_points.insert(1, 77.)
assign_character_action(rig, action, slot.handle)
scene.frame_set(42)
source_hash = curves.signature(action, slot.handle)
foreign_hash = curves.signature(action, other_slot.handle)
op = bpy.ops.character_designer.key_reduction_preview
assert op(operation='START', position_mm=0., rotation_degrees=0.) == {'FINISHED'}
assert preview.status(context)['after'] == 565
assert op(operation='UPDATE', position_mm=100., rotation_degrees=10.) == {'FINISHED'}
status = preview.status(context)
assert status['before'] == 565


def channels(clip):
    return curves._indexed(clip, slot.handle)


def key_values(curve):
    return {point.co.x: (point.co.y, point.type) for point in curve.keyframe_points}


def modifier_settings(curve):
    return [(mod.type, mod.mute, mod.mode_before, mod.mode_after,
             mod.cycles_before, mod.cycles_after, mod.use_influence, mod.use_restricted_range)
            for mod in curve.modifiers]


def samples(by_axis, path, time):
    return tuple(by_axis[path, axis].evaluate(time) for axis in range(3))


if expected == 'baseline':
    assert status['after'] == 565
    assert status['skipped']['Unsupported or mixed modifiers'] == 6, status
    assert op(operation='DISCARD') == {'FINISHED'}
    report = dict(before=565, after=565, skipped=status['skipped'])
else:
    assert 0 < status['after'] < 100, status
    assert 'Unsupported modifiers' not in status['skipped'], status
    assert curves.signature(action, slot.handle) == source_hash
    assert op(operation='ORIGINAL') == {'FINISHED'}
    assert rig.animation_data.action == action
    assert op(operation='REDUCED') == {'FINISHED'}
    reduced = rig[preview.SESSION]['preview']
    original_curves, reduced_curves = channels(action), channels(reduced)
    for key, original in original_curves.items():
        candidate = reduced_curves[key]
        before, after = key_values(original), key_values(candidate)
        assert modifier_settings(original) == modifier_settings(candidate), key
        assert candidate.extrapolation == original.extrapolation, key
        assert all(before[time] == value for time, value in after.items()), key
        ordered = sorted(before)
        protected = ordered[:1] + ordered[-1:] + [time for time in (42, 51) if time in before]
        if original.modifiers or original.extrapolation == 'LINEAR':
            protected += ordered[1:2] + ordered[-2:-1]
        assert all(time in after for time in protected), (key, protected, after)
        if key[0] in {location, rotation}:
            assert len(after) < len(before), key
        else:
            assert before == after, key
    max_mm = max_degrees = 0.
    for step in range(-3200, 4801):
        time = step / 16
        max_mm = max(max_mm, math.dist(samples(original_curves, location, time),
                                      samples(reduced_curves, location, time)) * 1000)
        first, second = (Euler(samples(by_axis, rotation, time)).to_quaternion()
                         for by_axis in (original_curves, reduced_curves))
        angle = first.rotation_difference(second).angle
        max_degrees = max(max_degrees, math.degrees(min(angle, abs(math.tau - angle))))
    assert max_mm <= 100.001 and max_degrees <= 10.001, (max_mm, max_degrees)

    labels = []
    layout = SimpleNamespace(prop=lambda *_: None, label=lambda *, text, **_: labels.append(text))
    draw = ui.CHARACTERDESIGNER_OT_key_reduction_preview.draw
    dialog = SimpleNamespace(layout=layout, position_mm=100., rotation_degrees=10.)
    draw(dialog, context)
    assert 'Key counts update after OK.' in labels, labels
    assert any(label.startswith('Reduced curves') for label in labels), labels

    assert op(operation='DISCARD') == {'FINISHED'}
    assert curves.signature(action, slot.handle) == source_hash
    assert op(operation='START', position_mm=100., rotation_degrees=10.) == {'FINISHED'}
    reduced = rig[preview.SESSION]['preview']
    expected_hash = curves.signature(reduced, slot.handle)
    assert op(operation='APPLY') == {'FINISHED'}
    assert curves.signature(action, slot.handle) == expected_hash
    assert curves.signature(action, other_slot.handle) == foreign_hash
    assert rig.animation_data.action == action and preview.SESSION not in rig

    # Keep the unsupported-stack guard, including when other axes can be reduced.
    channels(action)[rotation, 0].modifiers.new('NOISE')
    assert op(operation='START', position_mm=100., rotation_degrees=10.) == {'FINISHED'}
    assert preview.status(context)['skipped']['Unsupported modifiers'] == 3
    assert op(operation='DISCARD') == {'FINISHED'}
    report = dict(before=status['before'], after=status['after'], max_position_mm=max_mm,
                  max_rotation_degrees=max_degrees, dialog_labels=labels,
                  checked_frames=[-200, 300], originals_guarded_until_apply=True,
                  applied_preview_points=True, foreign_slot_unchanged=True)

report['version'] = character_designer.bl_info['version']
Path(output).write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
print('MIXED_CYCLES_KEY_REDUCTION_OK', expected, report['before'], report['after'])
