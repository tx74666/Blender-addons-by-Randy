"""Public Root/FK display actions, partial-mode visibility, and restoration."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import limb_ik, limb_ik_fk, bone_collections, root_control, limb_fk_visuals
from character_designer import body_controls_ui
import test_spine_ik_fk_blender as fixtures
import test_limb_ik_blender as base

character_designer.register()
rig, chain, torso = fixtures.fixture(posed=True)
before = fixtures.native(rig)
assert bpy.ops.character_designer.root_control(action='BUILD') == {'FINISHED'}
root = root_control.get_record(rig)
assert root and rig.data.bones.active.name == root['master']
assert root['master'] in rig.data.collections_all['Animation'].bones
assert rig.pose.bones[root['master']].color.palette == 'CUSTOM'
assert bpy.ops.character_designer.root_control(action='BUILD') == {'FINISHED'}
assert root_control.get_record(rig) == root
fixtures.spine._verify_pose(rig, before)

assert bpy.ops.character_designer.limb_fk_visuals(action='BUILD') == {'FINISHED'}
record = limb_fk_visuals.get_record(rig)
assert len(record['bindings']) == 8
assert bpy.ops.character_designer.limb_fk_visuals(action='BUILD') == {'FINISHED'}
assert limb_fk_visuals.get_record(rig) == record
fixtures.spine._verify_pose(rig, before)
for name in record['bindings']:
    assert rig.pose.bones[name].color.palette == 'CUSTOM'

inventory = limb_ik._validate_inventory(rig)
arm = inventory['rigs'][('ARM', 'L')]
target = rig.pose.bones[arm['target'].name]
target['ik_fk'] = .5
limb_ik_fk._update(bpy.context, rig)
bone_collections._frame_visibility(bpy.context.scene)
members = set(rig.data.collections_all['Animation'].bones.keys())
assert set(arm['chain']) <= members
assert {arm['target'].name, arm['pole'].name, root['master']} <= members
target['ik_fk'] = 1.0
limb_ik_fk._update(bpy.context, rig)
bone_collections._frame_visibility(bpy.context.scene)
assert not set(arm['chain']) & set(rig.data.collections_all['Animation'].bones.keys())

class Layout:
    def __init__(self): self.buttons = []; self.fields = []
    def row(self, **kw): return self
    def box(self): return self
    def label(self, **kw): pass
    def prop(self, data, name, **kw): self.fields.append(name)
    def operator(self, identifier, **kw):
        self.buttons.append((identifier, kw.get('text', '')))
        return SimpleNamespace()

layout = Layout()
body_controls_ui.draw_root_controls(layout, bpy.context)
body_controls_ui.draw_fk_visuals(layout, bpy.context)
assert ('character_designer.root_control', 'Root · Whole Body') in layout.buttons
assert '["uniform_scale"]' in layout.fields
assert ('character_designer.limb_fk_visuals', 'Remove FK Rings') in layout.buttons

assert bpy.ops.character_designer.limb_fk_visuals(action='FIT_IK') == {'FINISHED'}
assert limb_fk_visuals.has_ik_size_backup(rig)
assert bpy.ops.character_designer.limb_fk_visuals(action='RESTORE_IK') == {'FINISHED'}
assert not limb_fk_visuals.has_ik_size_backup(rig)
assert bpy.ops.character_designer.limb_fk_visuals(action='REMOVE') == {'FINISHED'}
assert limb_fk_visuals.get_record(rig) is None
assert bpy.ops.character_designer.root_control(action='REMOVE') == {'FINISHED'}
assert not root_control.get_record(rig)
fixtures.spine._verify_pose(rig, before)
limb_ik._validate_inventory(rig)
print('BODY_CONTROLS_UI_PASSED', flush=True)
