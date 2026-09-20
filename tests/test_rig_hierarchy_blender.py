"""Common Rig context precedes subcategory navigation, without duplicate tabs."""
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
import character_designer as cd
from character_designer import character_setup, bone_display


def test_structure():
    shared = (character_setup.CHARACTERDESIGNER_PT_character_setup,
              bone_display.CHARACTERDESIGNER_PT_bone_display, cd.CHARACTERDESIGNER_PT_rig_sections)
    assert all(p.bl_parent_id == cd.CHARACTERDESIGNER_PT_main.bl_idname for p in shared)
    assert [p.bl_order for p in shared] == [0, 1, 2]
    assert 'HIDE_HEADER' in shared[-1].bl_options
    assert 'set_rig_section' not in inspect.getsource(cd.CHARACTERDESIGNER_PT_main.draw)
    assert len({c.bl_idname for c in cd.CLASSES if issubclass(c, bpy.types.Panel)}) == len(
        [c for c in cd.CLASSES if issubclass(c, bpy.types.Panel)])


def test_switches_and_common_scope():
    dirty = bpy.data.is_dirty
    settings = bpy.context.window_manager.character_designer
    class Layout:
        def __init__(self): self.buttons = []
        def row(self, **kw): return self
        def operator(self, name, **kw):
            button = SimpleNamespace(**kw)
            self.buttons.append((name, button))
            return button
    for section in ('BODY', 'HAIR', 'SKIRT'):
        assert bpy.ops.character_designer.set_rig_section(section=section) == {'FINISHED'}
        assert character_setup.CHARACTERDESIGNER_PT_character_setup.poll(bpy.context)
        assert bone_display.CHARACTERDESIGNER_PT_bone_display.poll(bpy.context)
        assert cd.CHARACTERDESIGNER_PT_rig_sections.poll(bpy.context)
        layout = Layout()
        cd.CHARACTERDESIGNER_PT_rig_sections.draw(SimpleNamespace(layout=layout), bpy.context)
        assert [b.section for _, b in layout.buttons] == ['BODY', 'HAIR', 'SKIRT']
        assert [b.section for _, b in layout.buttons if b.depress] == [section]
    for page in ('WEIGHT', 'HAIR', 'ANIMATION', 'MISCELLANEOUS'):
        settings.ui_page = page
        assert not cd.CHARACTERDESIGNER_PT_rig_sections.poll(bpy.context)
        for panel in (character_setup.CHARACTERDESIGNER_PT_character_setup, bone_display.CHARACTERDESIGNER_PT_bone_display):
            assert panel.poll(bpy.context) == (page == 'WEIGHT')
    assert bpy.data.is_dirty == dirty


if __name__ == '__main__':
    cd.register()
    test_structure(); print('PASS common_context_then_sections')
    test_switches_and_common_scope(); print('PASS subroutes_shared_context_and_no_data_mutation')
    cd.unregister(); cd.register(); test_structure(); cd.unregister()
    print('PASS repeat_registration')
    print('RIG_HIERARCHY_PASS')
