"""BVH preview must preserve selection, scene timing, and the character's Action."""

from pathlib import Path
import sys
import tempfile

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
import character_designer
from character_designer.animation import import_motion_preview

BVH = """HIERARCHY
ROOT Hips
{
 OFFSET 0 100 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT Spine1
 {
  OFFSET 0 20 0
  CHANNELS 3 Zrotation Xrotation Yrotation
  End Site
  {
   OFFSET 0 20 0
  }
 }
}
MOTION
Frames: 3
Frame Time: 0.0333333333
0 100 0 0 0 0 0 0 0
0 100 10 0 0 0 0 0 0
0 100 20 0 0 0 0 0 0
"""

character_designer.register()
try:
    from character_designer import animation
    assert bpy.app.handlers.load_pre.count(animation._animation_load_pre) == 1
    character_designer.register()
    assert bpy.app.handlers.load_pre.count(animation._animation_load_pre) == 1
    scene = bpy.context.scene
    scene.render.fps = 24
    scene.frame_set(141)
    active = bpy.context.object
    selected = set(bpy.context.selected_objects)
    original_end = scene.frame_end
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "preview.bvh"
        path.write_text(BVH, encoding="utf-8")
        source = import_motion_preview(bpy.context, path, 10)
    assert source.type == "ARMATURE"
    assert source.get("character_designer_motion_preview")
    assert source.animation_data.action
    assert bpy.context.object == active
    assert set(bpy.context.selected_objects) == selected
    assert scene.frame_current == 141
    assert scene.render.fps == 24 and scene.frame_end == original_end
    assert tuple(round(v, 3) for v in source.animation_data.action.frame_range) == (10.0, 11.6)
    character_designer._validate_registration_integrity()
    print("ANIMATION_IMPORT_ALL_CHECKS_PASSED")
finally:
    character_designer.unregister()
    assert animation._animation_load_pre not in bpy.app.handlers.load_pre
