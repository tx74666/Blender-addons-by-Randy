"""One registered Rig Setup subtree with stable read-only section navigation."""
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
import character_designer as cd
from character_designer import (character_setup, bone_display, limb_ik, torso_ui,
    finger_bones, forearm_twist, spline_ik_setup, hair_bones, hair_motion_ui, skirt)


def rig_children():
    """The reviewed visible organization, independent of tuple/draw internals."""
    return (
        (character_setup.CHARACTERDESIGNER_PT_character_setup, 'Character & Bone Mapping', 0),
        (limb_ik.CHARACTERDESIGNER_PT_limb_ik, 'Body Controls', 10),
        (torso_ui.CHARACTERDESIGNER_PT_torso_controls, 'Back & Chest', 20),
        (finger_bones.CHARACTERDESIGNER_PT_fingers, 'Finger Controls', 30),
        (forearm_twist.CHARACTERDESIGNER_PT_forearm_twist, 'Forearm Twist Correction', 40),
        (spline_ik_setup.CHARACTERDESIGNER_PT_spline_ik_setup, 'Curve Chain Controls', 50),
        (hair_bones.CHARACTERDESIGNER_PT_hair_bones, 'Hair Strands & Binding', 10),
        (hair_motion_ui.CHARACTERDESIGNER_PT_hair_motion, 'Hair Motion Settings', 20),
        (skirt.CHARACTERDESIGNER_PT_skirt_setup, 'Skirt Controls & Binding', 10),
    )


def routing_state():
    """Actual native context/data headers which navigation must not edit."""
    active = bpy.context.view_layer.objects.active
    return (bpy.data.is_dirty, bpy.context.mode, active,
        tuple(bpy.context.selected_objects), bpy.context.scene.frame_current,
        bpy.context.scene.frame_subframe,
        tuple((obj.as_pointer(), obj.name, obj.mode, obj.data.as_pointer() if obj.data else None,
            tuple(tuple(row) for row in obj.matrix_world),
            obj.animation_data.action if obj.animation_data else None,
            obj.animation_data.action_slot_handle if obj.animation_data else None)
            for obj in bpy.data.objects),
        tuple((scene.as_pointer(), scene.name) for scene in bpy.data.scenes))


def test_structure():
    main, setup = cd.CHARACTERDESIGNER_PT_main, cd.CHARACTERDESIGNER_PT_rig_sections
    assert setup.bl_parent_id == main.bl_idname and setup.bl_order == 2
    assert setup.bl_label == 'Rig Setup' and 'HIDE_HEADER' not in getattr(setup, 'bl_options', set())
    assert cd.CLASSES.index(setup) == cd.CLASSES.index(main) + 1
    assert bone_display.CHARACTERDESIGNER_PT_bone_display.bl_parent_id == main.bl_idname
    assert bone_display.CHARACTERDESIGNER_PT_bone_display.bl_order == 1
    descendants = (limb_ik.CHARACTERDESIGNER_PT_limb_ik_target_rotation,
                   limb_ik.CHARACTERDESIGNER_PT_limb_ik_control_visual,
                   limb_ik.CHARACTERDESIGNER_PT_limb_ik_direct_preroll)
    for panel, label, order in rig_children():
        assert panel.bl_parent_id == setup.bl_idname, panel.__name__
        assert (panel.bl_label, panel.bl_order) == (label, order), panel.__name__
        assert cd.CLASSES.index(setup) < cd.CLASSES.index(panel)
    for panel in descendants:
        assert panel.bl_parent_id == limb_ik.CHARACTERDESIGNER_PT_limb_ik.bl_idname
        assert cd.CLASSES.index(limb_ik.CHARACTERDESIGNER_PT_limb_ik) < cd.CLASSES.index(panel)
    # Prove native registered ownership too, rather than only class attributes.
    for panel in (main, setup, *(row[0] for row in rig_children()), *descendants):
        assert cd.CLASSES.count(panel) == 1
        assert bpy.types.Panel.bl_rna_get_subclass_py(panel.bl_rna.identifier, None) is panel
    assert 'set_rig_section' not in inspect.getsource(cd.CHARACTERDESIGNER_PT_main.draw)
    panels = [cls for cls in cd.CLASSES if issubclass(cls, bpy.types.Panel)]
    assert len({cls.bl_idname for cls in panels}) == len(panels)


def test_switches_and_common_scope():
    settings = bpy.context.window_manager.character_designer
    class Layout:
        def __init__(self): self.buttons, self.events = [], []
        def row(self, **kw): return self
        def label(self, **kw): self.events.append(('label', kw.get('text', '')))
        def operator(self, name, **kw):
            button = SimpleNamespace(**kw)
            self.buttons.append((name, button))
            self.events.append(('operator', name))
            return button
    before = routing_state()
    for section in ('BODY', 'HAIR', 'SKIRT'):
        assert bpy.ops.character_designer.set_rig_section(section=section) == {'FINISHED'}
        assert character_setup.CHARACTERDESIGNER_PT_character_setup.poll(bpy.context)
        assert bone_display.CHARACTERDESIGNER_PT_bone_display.poll(bpy.context)
        assert cd.CHARACTERDESIGNER_PT_rig_sections.poll(bpy.context)
        layout = Layout()
        cd.CHARACTERDESIGNER_PT_rig_sections.draw(SimpleNamespace(layout=layout), bpy.context)
        assert layout.events[0] == ('label', 'Create and adjust character controls.')
        assert [b.section for _, b in layout.buttons] == ['BODY', 'HAIR', 'SKIRT']
        assert [b.section for _, b in layout.buttons if b.depress] == [section]
    for page in ('MODELING', 'WEIGHT', 'HAIR', 'ANIMATION', 'MISCELLANEOUS'):
        settings.ui_page = page
        assert not cd.CHARACTERDESIGNER_PT_rig_sections.poll(bpy.context)
        assert not character_setup.CHARACTERDESIGNER_PT_character_setup.poll(bpy.context)
        assert bone_display.CHARACTERDESIGNER_PT_bone_display.poll(bpy.context) == (page == 'WEIGHT')
    assert routing_state() == before, 'Rig navigation/draw changed native context, Action binding or Scene data headers'


if __name__ == '__main__':
    cd.register()
    test_structure(); print('PASS registered_rig_setup_parent_chain_labels_order_unique')
    test_switches_and_common_scope(); print('PASS subroutes_shared_context_and_no_data_mutation')
    cd.unregister(); cd.register(); test_structure(); cd.unregister()
    print('PASS repeat_registration')
    print('RIG_HIERARCHY_PASS')
