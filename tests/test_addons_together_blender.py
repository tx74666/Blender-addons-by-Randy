"""Check repository imports and both add-ons' registration in either order.

Run only in factory-startup background Blender. No export operator is invoked.
"""

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy


ADDONS = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDONS))
rr = importlib.import_module("random_realm_builder_exporter")
cd = importlib.import_module("character_designer")
for module in (rr, cd):
    assert Path(module.__file__).resolve().is_relative_to(ADDONS.resolve()), module.__file__


class Layout:
    def __init__(self):
        self.buttons = []
    def box(self): return self
    def row(self, **_kwargs): return self
    def operator(self, name, **kwargs):
        result = SimpleNamespace()
        self.buttons.append((name, kwargs.get("text"), result))
        return result


class RRPanelProbe:
    draw_page_tabs = rr.RR_PT_builder_exporter.draw_page_tabs
    draw_page_tab = rr.RR_PT_builder_exporter.draw_page_tab
    def __init__(self):
        self.layout, self.pages = Layout(), []
    def draw_exporter_page(self, *_args): self.pages.append("EXPORTER")
    def draw_bake_page(self, *_args): self.pages.append("BAKE")
    def draw_modeling_page(self, *_args): self.pages.append("MODELING")
    def draw_layout_page(self, *_args): self.pages.append("LAYOUT")


def assert_animation_entry_retired():
    settings = bpy.context.scene.rr_builder_export_settings
    settings.ui_page = "ANIMATION"  # An existing saved enum still loads.
    probe = RRPanelProbe()
    rr.RR_PT_builder_exporter.draw(probe, bpy.context)
    tabs = [button.page for op, _label, button in probe.layout.buttons if op == "rr_builder.set_ui_page"]
    assert tabs == ["EXPORTER", "BAKE", "MODELING", "LAYOUT"]
    assert probe.pages == ["EXPORTER"]
    assert settings.ui_page == "ANIMATION", "Drawing must not rewrite saved settings"
    assert bpy.ops.rr_builder.set_ui_page(page="ANIMATION") == {"FINISHED"}
    assert settings.ui_page == "EXPORTER"
    # Keep legacy script identifiers without presenting duplicate F3 entries.
    # Do not invoke these operators: opening Animation.blend or exporting is
    # deliberately outside this registration-only regression.
    for cls in (rr.RR_OT_open_animation_blend, rr.RR_OT_open_unity_animation_import_folder,
                rr.RR_OT_request_unity_animation_import):
        assert "INTERNAL" in cls.bl_options
        group, name = cls.bl_idname.split(".")
        assert getattr(getattr(bpy.ops, group), name).get_rna_type()
        assert callable(cls.execute)

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
        assert_animation_entry_retired()
        print("BOTH_ADDONS_ENABLED", [module.bl_info["name"] for module in order])
    finally:
        for module in reversed(registered):
            module.unregister()
    assert not hasattr(bpy.types.Scene, "rr_builder_export_settings")
    assert not hasattr(bpy.types.WindowManager, "character_designer")

print("ADDONS_TOGETHER_ALL_CHECKS_PASSED")
