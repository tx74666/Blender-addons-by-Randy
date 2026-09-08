"""Check repository imports and both add-ons' registration in either order.

Run only in factory-startup background Blender. No export operator is invoked.
"""

import importlib
from pathlib import Path
import sys

import bpy


ADDONS = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDONS))
rr = importlib.import_module("random_realm_builder_exporter")
cd = importlib.import_module("character_designer")
for module in (rr, cd):
    assert Path(module.__file__).resolve().is_relative_to(ADDONS.resolve()), module.__file__

for order in ((rr, cd), (cd, rr)):
    registered = []
    try:
        for module in order:
            module.register()
            registered.append(module)
        assert hasattr(bpy.context.scene, "rr_builder_export_settings")
        assert hasattr(bpy.context.window_manager, "character_designer")
        assert bpy.types.Panel.bl_rna_get_subclass_py("RR_PT_builder_exporter") is not None
        assert bpy.ops.character_designer.set_ui_page(page="CLOTHING") == {"FINISHED"}
        print("BOTH_ADDONS_ENABLED", [module.bl_info["name"] for module in order])
    finally:
        for module in reversed(registered):
            module.unregister()
    assert not hasattr(bpy.types.Scene, "rr_builder_export_settings")
    assert not hasattr(bpy.types.WindowManager, "character_designer")

print("ADDONS_TOGETHER_ALL_CHECKS_PASSED")
