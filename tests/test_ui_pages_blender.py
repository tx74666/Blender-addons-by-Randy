"""Regression checks for the RR Helper-style CDesigner page grid."""

import inspect
import sys
from pathlib import Path

import bpy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADDONS_ROOT = PROJECT_ROOT / "addons"
if str(ADDONS_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDONS_ROOT))

import character_designer
from character_designer import (
    animation,
    character_setup,
    delta_symmetry,
    forearm_twist,
    limb_ik,
    reference_views,
    selected_bone_weights,
    skirt,
    spline_ik_setup,
    weight_symmetry,
)
from character_designer.ui_constants import (
    UI_PAGE_ANIMATION,
    UI_PAGE_CLOTHING,
    UI_PAGE_HAIR,
    UI_PAGE_MISC,
    UI_PAGE_RIG,
    UI_PAGE_WEIGHT,
)


def assert_only_page(page, *, weight=False, modeling=False, rig=False, reference=False,
                     clothing=False, motion=False):
    settings = bpy.context.window_manager.character_designer
    limb_settings = bpy.context.window_manager.character_designer_limb_ik
    result = bpy.ops.character_designer.set_ui_page(page=page)
    if "FINISHED" not in result or settings.ui_page != page:
        raise AssertionError(f"Could not switch to CDesigner page {page}")

    actual = {
        "quick_bind": character_setup.CHARACTERDESIGNER_PT_quick_bind.poll(bpy.context),
        "character_setup": character_setup.CHARACTERDESIGNER_PT_character_setup.poll(bpy.context),
        "animation": animation.CHARACTERDESIGNER_PT_animation.poll(bpy.context),
        "weight_tools": selected_bone_weights.CHARACTERDESIGNER_PT_weight_tools.poll(
            bpy.context
        ),
        "weight_symmetry": weight_symmetry.CHARACTERDESIGNER_PT_weight_symmetry.poll(
            bpy.context
        ),
        "modeling": delta_symmetry.CHARACTERDESIGNER_PT_delta_symmetry.poll(
            bpy.context
        ),
        "rig": spline_ik_setup.CHARACTERDESIGNER_PT_spline_ik_setup.poll(bpy.context),
        "limb_ik": limb_ik.CHARACTERDESIGNER_PT_limb_ik.poll(bpy.context),
        "forearm_twist": forearm_twist.CHARACTERDESIGNER_PT_forearm_twist.poll(bpy.context),
        "limb_preroll": limb_ik.CHARACTERDESIGNER_PT_limb_ik_direct_preroll.poll(
            bpy.context
        ),
        "reference": reference_views.CHARACTERDESIGNER_PT_reference_views.poll(
            bpy.context
        ),
        "clothing": skirt.CHARACTERDESIGNER_PT_skirt_setup.poll(bpy.context),
    }
    expected = {
        "quick_bind": weight,
        "character_setup": page in {UI_PAGE_WEIGHT, UI_PAGE_HAIR, UI_PAGE_CLOTHING, UI_PAGE_RIG},
        "animation": motion,
        "weight_tools": weight,
        "weight_symmetry": weight,
        "modeling": modeling,
        "rig": rig,
        "limb_ik": rig,
        "forearm_twist": rig,
        "limb_preroll": rig and limb_settings.build_method == "DIRECT_PREROLL",
        "reference": reference,
        "clothing": clothing,
    }
    if actual != expected:
        raise AssertionError(f"Page {page} routed panels incorrectly: {actual}")


def assert_weight_panel_forwards_full_auto(expected):
    class OperatorProxy:
        normalize_affected_deform_weights = None

    class LayoutProxy:
        def __init__(self):
            self.operator_proxy = OperatorProxy()

        def label(self, **_kwargs):
            return None

        def prop(self, *_args, **_kwargs):
            return None

        def operator(self, *_args, **_kwargs):
            return self.operator_proxy

    settings = bpy.context.window_manager.character_designer
    settings.normalize_affected_deform_weights = expected
    layout = LayoutProxy()
    panel = type("PanelProxy", (), {"layout": layout})()
    selected_bone_weights.CHARACTERDESIGNER_PT_weight_tools.draw(
        panel,
        bpy.context,
    )
    actual = layout.operator_proxy.normalize_affected_deform_weights
    if actual is not expected:
        raise AssertionError(
            f"Weight panel forwarded Full Auto={actual!r}, expected {expected!r}"
        )


def assert_compact_limb_ik_panel():
    settings = bpy.context.window_manager.character_designer_limb_ik
    preroll_property = limb_ik.CharacterDesignerLimbIKState.bl_rna.properties[
        "direct_preroll_json"
    ]
    if not preroll_property.is_skip_save:
        raise AssertionError("Direct Pre-Roll result must remain session-only")
    selection_property = limb_ik.CharacterDesignerLimbIKState.bl_rna.properties[
        "selected_limb"
    ]
    items = tuple(
        (item.identifier, item.name) for item in selection_property.enum_items
    )
    expected_items = (
        ("LEFT_ARM", "Left Arm"),
        ("RIGHT_ARM", "Right Arm"),
        ("LEFT_LEG", "Left Leg"),
        ("RIGHT_LEG", "Right Leg"),
    )
    if items != expected_items:
        raise AssertionError(f"Unexpected Limb IK selector items: {items}")
    if not selection_property.is_skip_save:
        raise AssertionError("The Limb IK selector must remain session-only")

    build_method_property = limb_ik.CharacterDesignerLimbIKState.bl_rna.properties[
        "build_method"
    ]
    build_methods = tuple(
        (item.identifier, item.name) for item in build_method_property.enum_items
    )
    expected_build_methods = (
        ("ROLL_DECOUPLED", "Stable (MCH/ORI)"),
        ("DIRECT_PREROLL", "Direct Pre-Roll (Minimal)"),
    )
    if build_methods != expected_build_methods:
        raise AssertionError(f"Unexpected Limb IK build methods: {build_methods}")
    if build_method_property.default != "ROLL_DECOUPLED":
        raise AssertionError("Stable (MCH/ORI) must be the default Limb IK build method")
    if not build_method_property.is_skip_save:
        raise AssertionError("The Limb IK build method must remain session-only")
    if settings.build_method != "ROLL_DECOUPLED":
        raise AssertionError("The Limb IK build method instance did not default to Stable")

    expected_direction_properties = {
        "LEFT_ARM": ("left_arm_pole_direction", (0.0, 1.0, 0.0)),
        "RIGHT_ARM": ("right_arm_pole_direction", (0.0, 1.0, 0.0)),
        "LEFT_LEG": ("left_leg_pole_direction", (0.0, -1.0, 0.0)),
        "RIGHT_LEG": ("right_leg_pole_direction", (0.0, -1.0, 0.0)),
    }
    for selection, (property_name, expected_default) in expected_direction_properties.items():
        direction_property = limb_ik.CharacterDesignerLimbIKState.bl_rna.properties[property_name]
        actual_default = tuple(float(value) for value in direction_property.default_array)
        if direction_property.array_length != 3 or actual_default != expected_default:
            raise AssertionError(
                f"{selection} Pole Direction is not a three-axis {expected_default} default: "
                f"length={direction_property.array_length}, default={actual_default}"
            )
        if not direction_property.is_skip_save:
            raise AssertionError(f"{selection} Pole Direction must remain session-only")

    dirty_before_toggle = bpy.data.is_dirty
    settings.selected_limb = "RIGHT_LEG"
    settings.right_leg_pole_direction = (0.2, -0.9, 0.15)
    settings.build_method = "DIRECT_PREROLL"
    settings.build_method = "ROLL_DECOUPLED"
    if bpy.data.is_dirty != dirty_before_toggle:
        raise AssertionError("Changing the Limb IK selector/direction/build method dirtied the .blend")

    class LayoutProxy:
        def __init__(self):
            self.properties = []
            self.operators = []
            self.alert = False

        def prop(self, _data, property_name, **_kwargs):
            self.properties.append(property_name)

        def prop_search(self, _data, property_name, *_args, **_kwargs):
            self.properties.append(property_name)

        def operator(self, operator_name, **_kwargs):
            self.operators.append(operator_name)
            return type("OperatorProxy", (), {})()

        def row(self, **_kwargs):
            return self

        def column(self, **_kwargs):
            return self

        def label(self, **_kwargs):
            return None

    expected_fields = {
        "LEFT_ARM": ("left_arm_upper", "left_arm_lower", "left_arm_end", "left_arm_pole_direction"),
        "RIGHT_ARM": ("right_arm_upper", "right_arm_lower", "right_arm_end", "right_arm_pole_direction"),
        "LEFT_LEG": ("left_leg_upper", "left_leg_lower", "left_leg_end", "left_leg_pole_direction"),
        "RIGHT_LEG": ("right_leg_upper", "right_leg_lower", "right_leg_end", "right_leg_pole_direction"),
    }
    expected_operators = (
        "character_designer.limb_ik_analyze",
        "character_designer.limb_ik_default_pole_direction",
        "character_designer.limb_ik_build_selected",
        "character_designer.limb_ik_build_all",
        "character_designer.limb_ik_auto_align_target",
        "character_designer.limb_ik_rebuild",
        "character_designer.limb_ik_remove",
        "character_designer.simplify_bone_collections",
    )
    old_armature = settings.armature
    settings.armature = None
    try:
        for selection, fields in expected_fields.items():
            settings.selected_limb = selection
            layout = LayoutProxy()
            panel = type("PanelProxy", (), {"layout": layout})()
            limb_ik.CHARACTERDESIGNER_PT_limb_ik.draw(panel, bpy.context)
            if tuple(layout.properties) != ("selected_limb", "build_method", *fields):
                raise AssertionError(
                    f"Limb IK {selection} drew unexpected fields: {layout.properties}"
                )
            if tuple(layout.operators) != expected_operators:
                raise AssertionError(
                    f"Limb IK drew unexpected operators: {layout.operators}"
                )
            if "pole_distance_ratio" in layout.properties:
                raise AssertionError("The compact Limb IK panel exposed Pole Distance")
            if "character_designer.limb_ik_flip_pole" in layout.operators:
                raise AssertionError("The compact Limb IK panel exposed Flip Pole")
            if layout.alert:
                raise AssertionError("Remove was red without an active owned Limb IK rig")

        legacy_operators = {
            "character_designer.limb_ik_build_arm",
            "character_designer.limb_ik_build_leg",
        }
        if legacy_operators.intersection(layout.operators):
            raise AssertionError("The compact Limb IK panel exposed legacy Build Arms/Legs")

        old_preroll = settings.direct_preroll_json
        old_build_method = settings.build_method
        page_settings = bpy.context.window_manager.character_designer
        old_page = page_settings.ui_page
        try:
            settings.direct_preroll_json = ""
            page_settings.ui_page = UI_PAGE_RIG
            settings.build_method = "ROLL_DECOUPLED"
            if limb_ik.CHARACTERDESIGNER_PT_limb_ik_direct_preroll.poll(bpy.context):
                raise AssertionError("Direct Pre-Roll Lab was visible in Stable mode")

            settings.build_method = "DIRECT_PREROLL"
            page_settings.ui_page = UI_PAGE_HAIR
            if limb_ik.CHARACTERDESIGNER_PT_limb_ik_direct_preroll.poll(bpy.context):
                raise AssertionError("Direct Pre-Roll Lab was visible outside the Rig page")
            page_settings.ui_page = UI_PAGE_RIG
            if not limb_ik.CHARACTERDESIGNER_PT_limb_ik_direct_preroll.poll(bpy.context):
                raise AssertionError("Direct Pre-Roll Lab was hidden in Direct mode on the Rig page")

            lab_layout = LayoutProxy()
            lab_panel = type("PanelProxy", (), {"layout": lab_layout})()
            limb_ik.CHARACTERDESIGNER_PT_limb_ik_direct_preroll.draw(
                lab_panel,
                bpy.context,
            )
            if tuple(lab_layout.operators) != (
                "character_designer.limb_ik_direct_preroll_check",
            ):
                raise AssertionError(
                    f"Direct Pre-Roll Lab drew unexpected operators: {lab_layout.operators}"
                )
        finally:
            settings.direct_preroll_json = old_preroll
            settings.build_method = old_build_method
            page_settings.ui_page = old_page

        custom_directions = {
            "left_arm_pole_direction": (0.2, 0.8, -0.1),
            "right_arm_pole_direction": (-0.1, 0.9, 0.2),
            "left_leg_pole_direction": (0.15, -0.85, 0.1),
            "right_leg_pole_direction": (-0.2, -0.75, -0.15),
        }
        for property_name, value in custom_directions.items():
            setattr(settings, property_name, value)
        untouched = {
            property_name: tuple(getattr(settings, property_name))
            for property_name in custom_directions
            if property_name != "right_leg_pole_direction"
        }
        settings.selected_limb = "RIGHT_LEG"
        dirty_before_reset = bpy.data.is_dirty
        reset_proxy = type("ResetOperatorProxy", (), {"report": lambda _self, *_args: None})()
        reset_result = limb_ik.CHARACTERDESIGNER_OT_limb_ik_default_pole_direction.execute(
            reset_proxy,
            bpy.context,
        )
        if reset_result != {"FINISHED"}:
            raise AssertionError(f"Default Direction failed: {reset_result}")
        if tuple(settings.right_leg_pole_direction) != (0.0, -1.0, 0.0):
            raise AssertionError("Default Direction did not restore the selected Right Leg forward to -Y")
        for property_name, before in untouched.items():
            if tuple(getattr(settings, property_name)) != before:
                raise AssertionError(f"Default Direction changed non-selected {property_name}")
        if bpy.data.is_dirty != dirty_before_reset:
            raise AssertionError("Default Direction dirtied the .blend")

        class ArmatureDataProxy(dict):
            def __init__(self, *args, bones=(), **kwargs):
                super().__init__(*args, **kwargs)
                self.bones = bones

        armature_id = "test-armature-id"
        owned_side_bone = {
            limb_ik.OWNER_KEY: limb_ik.OWNER_VALUE,
            limb_ik.ARMATURE_ID_KEY: armature_id,
            limb_ik.RIG_ID_KEY: "test-rig-id",
        }
        fake_data = ArmatureDataProxy(
            {limb_ik.ARMATURE_ID_KEY: armature_id},
            bones=(owned_side_bone,),
        )
        fake_armature = type("ArmatureProxy", (), {"type": "ARMATURE", "data": fake_data})()
        fake_context = type("ContextProxy", (), {"object": fake_armature})()
        if not limb_ik._active_has_owned_side_rig(fake_context):
            raise AssertionError("Owned side rig did not activate destructive Remove styling")
        fake_data.bones = ()
        if limb_ik._active_has_owned_side_rig(fake_context):
            raise AssertionError("Removed side rig left destructive Remove styling active")

        original_owned_check = limb_ik._active_has_owned_side_rig
        limb_ik._active_has_owned_side_rig = lambda _context: True
        try:
            layout = LayoutProxy()
            panel = type("PanelProxy", (), {"layout": layout})()
            limb_ik.CHARACTERDESIGNER_PT_limb_ik.draw(panel, bpy.context)
            if not layout.alert:
                raise AssertionError("Owned rig did not draw Remove with red alert styling")
        finally:
            limb_ik._active_has_owned_side_rig = original_owned_check
    finally:
        settings.armature = old_armature
        settings.build_method = "ROLL_DECOUPLED"
        for _selection, (property_name, expected_default) in expected_direction_properties.items():
            setattr(settings, property_name, expected_default)


def main():
    character_designer.register()
    try:
        if hasattr(bpy.types, "CHARACTERDESIGNER_PT_weight_flow") or hasattr(
            bpy.types, "CHARACTER_DESIGNER_OT_weight_flow"
        ):
            raise AssertionError("The removed Weight Flow feature is still registered")
        settings = bpy.context.window_manager.character_designer
        if settings.ui_page != UI_PAGE_HAIR:
            raise AssertionError("Hair must be the default CDesigner page")

        ui_page_property = character_designer.CharacterDesignerState.bl_rna.properties[
            "ui_page"
        ]
        identifiers = tuple(item.identifier for item in ui_page_property.enum_items)
        expected_identifiers = (
            UI_PAGE_HAIR,
            UI_PAGE_WEIGHT,
            UI_PAGE_RIG,
            UI_PAGE_CLOTHING,
            UI_PAGE_ANIMATION,
            UI_PAGE_MISC,
        )
        if identifiers != expected_identifiers:
            raise AssertionError(f"Unexpected CDesigner pages: {identifiers}")
        if not ui_page_property.is_skip_save:
            raise AssertionError("CDesigner page state must not dirty or persist in .blend")

        normalize_property = character_designer.CharacterDesignerState.bl_rna.properties[
            "normalize_affected_deform_weights"
        ]
        if not normalize_property.default:
            raise AssertionError("Full Auto Blend should be enabled for the UI by default")
        if not normalize_property.is_skip_save:
            raise AssertionError(
                "Affected-weight normalization must not dirty or persist in .blend"
            )
        if not settings.normalize_affected_deform_weights:
            raise AssertionError("The Full Auto UI state instance did not default on")
        operator_property = bpy.ops.character_designer.auto_weight_selected_bones.get_rna_type().properties[
            "normalize_affected_deform_weights"
        ]
        if operator_property.default or not operator_property.is_skip_save:
            raise AssertionError(
                "The direct operator flag must stay opt-in and transient for API compatibility"
            )

        dirty_before_toggle = bpy.data.is_dirty
        settings.normalize_affected_deform_weights = False
        if bpy.data.is_dirty != dirty_before_toggle:
            raise AssertionError("Turning Full Auto off dirtied the .blend")
        settings.normalize_affected_deform_weights = True
        if bpy.data.is_dirty != dirty_before_toggle:
            raise AssertionError("Turning Full Auto back on dirtied the .blend")
        assert_weight_panel_forwards_full_auto(True)
        assert_weight_panel_forwards_full_auto(False)
        assert_compact_limb_ik_panel()
        settings.normalize_affected_deform_weights = True
        assert_only_page(UI_PAGE_WEIGHT, weight=True)
        assert_only_page(UI_PAGE_HAIR)
        assert_only_page(UI_PAGE_WEIGHT, weight=True)
        if not settings.normalize_affected_deform_weights:
            raise AssertionError("The checkbox did not survive page redraws in-session")
        settings.normalize_affected_deform_weights = False

        source = inspect.getsource(character_designer._draw_page_tabs)
        if "first_row" not in source or "second_row" not in source:
            raise AssertionError("CDesigner page grid is not split into two aligned rows")
        tab_source = inspect.getsource(character_designer._draw_page_tab)
        if "depress=active_page == page" not in tab_source:
            raise AssertionError("The active CDesigner page is not visibly depressed")

        assert_only_page(UI_PAGE_HAIR)
        assert_only_page(UI_PAGE_WEIGHT, weight=True)
        assert_only_page(UI_PAGE_RIG, rig=True)
        assert_only_page(UI_PAGE_CLOTHING, clothing=True)
        assert_only_page(UI_PAGE_ANIMATION, motion=True)
        assert_only_page(UI_PAGE_MISC, modeling=True, reference=True)
        print("PASS CDesigner UI Pages 3 tests")
    finally:
        character_designer.unregister()


if __name__ == "__main__":
    main()
