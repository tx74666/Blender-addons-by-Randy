"""Read native Panel ownership in an isolated trusted Blender process."""
import json
from pathlib import Path
import sys

import _bpy
import bpy


class RR_PT_sidebar_owner_probe(bpy.types.Panel):
    bl_idname = "RR_PT_sidebar_owner_probe"
    bl_label = "Owner Probe"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RR Probe"

    def draw(self, context):
        self.layout.label(text="Read-only ownership probe")


old_owner = _bpy._bl_owner_id_get()
try:
    _bpy._bl_owner_id_set("rr.foreign.fixture")
    bpy.utils.register_class(RR_PT_sidebar_owner_probe)
    result = {
        "blender": bpy.app.version_string,
        "owner": getattr(RR_PT_sidebar_owner_probe, "bl_owner_id", None),
        "owner_in_class_dict": "bl_owner_id" in RR_PT_sidebar_owner_probe.__dict__,
        "registered": RR_PT_sidebar_owner_probe.is_registered,
    }
    print("RR_SIDEBAR_OWNER_PROBE " + json.dumps(result), flush=True)
    assert result["owner"] == "rr.foreign.fixture", result
finally:
    _bpy._bl_owner_id_set(old_owner)
    if RR_PT_sidebar_owner_probe.is_registered:
        bpy.utils.unregister_class(RR_PT_sidebar_owner_probe)
