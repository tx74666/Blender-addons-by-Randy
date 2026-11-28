"""Exercise Original / Edited with Blender Graph Editor's native Decimate."""

import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

package, result = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, package)
import character_designer
from character_designer import animation_preview_curves as curves
from character_designer import animation_preview_session as session
from character_designer import animation_preview_ui as ui

character_designer.register()
ctx = bpy.context
rig = bpy.data.objects.new('Comparison Rig', bpy.data.armatures.new('Comparison Bones'))
ctx.scene.collection.objects.link(rig)
ctx.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
for name in ('Wrist', 'Foot'):
    bone = rig.data.edit_bones.new(name)
    bone.tail.z = 1.
bpy.ops.object.mode_set(mode='POSE')
wrist, foot = rig.pose.bones
wrist.select, foot.select = True, False
for i in range(1, 62):
    foot.location.x = math.sin(i / 9.)
    wrist.location.x = math.cos(i / 7.)
    for bone in (wrist, foot):
        bone.keyframe_insert('location', index=0, frame=i)
ctx.scene.frame_set(21, subframe=.25)
source = rig.animation_data.action
slot = rig.animation_data.action_slot_handle
source.use_fake_user = True
other = source.slots.new('OBJECT', 'Other')
source.layers[0].strips[0].channelbag(other, ensure=True).fcurves.new('location').keyframe_points.insert(1, 9)
before = curves.signature(source, slot)
foreign = curves.signature(source, other.handle)
actions = set(bpy.data.actions)
op = bpy.ops.character_designer.key_reduction_preview


def edited():
    return rig[session.SESSION]['preview']


class Layout:
    def row(self, **_):
        return self

    box = row

    def label(self, *, text, **_):
        labels.append(text)

    def operator(self, _, *, text, **_kwargs):
        button = SimpleNamespace()
        buttons[text] = button
        return button


def draw():
    labels.clear()
    buttons.clear()
    ui.draw(Layout(), ctx, session.status(ctx))


labels, buttons = [], {}
assert op(operation='START') == {'FINISHED'}
assert rig.animation_data.action == edited() and edited() != source
assert curves.signature(edited(), slot) == before
assert session.status(ctx)['edit_only']
draw()
assert list(buttons) == ['Original', 'Edited', 'Apply Edits', 'Discard'] and not labels

area = next(a for a in ctx.screen.areas if a.type == 'DOPESHEET_EDITOR')
area.type = 'GRAPH_EDITOR'
area.spaces.active.dopesheet.show_only_selected = False
region = next(r for r in area.regions if r.type == 'WINDOW')
work = curves._indexed(edited(), slot)
with ctx.temp_override(area=area, region=region):
    for (path, _), curve in work.items():
        curve.select = path == foot.path_from_id('location')
        for point in curve.keyframe_points:
            point.select_control_point = curve.select
    assert bpy.ops.graph.decimate(mode='RATIO', factor=.5) == {'FINISHED'}

assert curves.signature(source, slot) == before
count = len(work[foot.path_from_id('location'), 0].keyframe_points)
assert 2 < count < 61, count
assert len(work[wrist.path_from_id('location'), 0].keyframe_points) == 61
draw()
assert labels == [f'Keys on changed curves  61 → {count}  (−{(61-count)/61:.1%})'], labels
summary = labels[0]

assert op(operation='ORIGINAL') == {'FINISHED'} and rig.animation_data.action == source
draw()
assert list(buttons) == ['Original', 'Edited', 'Keep Original']
assert buttons['Keep Original'].operation == 'DISCARD'
assert op(operation='REDUCED') == {'FINISHED'} and rig.animation_data.action == edited()
assert ctx.scene.frame_current == 21 and ctx.scene.frame_subframe == .25
changed = curves.signature(edited(), slot)
assert op(operation='APPLY') == {'FINISHED'}
assert curves.signature(source, slot) == changed and curves.signature(source, other.handle) == foreign
assert rig.animation_data.action == source and session.SESSION not in rig and set(bpy.data.actions) == actions

wrist.select = False
assert not ctx.selected_pose_bones
assert op(operation='START') == {'FINISHED'}
curves._indexed(edited(), slot)[foot.path_from_id('location'), 0].keyframe_points[2].co.y += .5
assert op(operation='ORIGINAL') == {'FINISHED'}
assert op(operation='DISCARD') == {'FINISHED'}
assert curves.signature(source, slot) == changed and set(bpy.data.actions) == actions
Path(result).write_text(json.dumps(
    dict(version=character_designer.bl_info['version'], before=61, after=count, summary=summary,
         native_decimate=True, original_preserved_until_apply=True, keep_original_passed=True),
    ensure_ascii=False, indent=2), encoding='utf-8')
print('PASS native Bézier decimation and Original / Edited comparison.')
