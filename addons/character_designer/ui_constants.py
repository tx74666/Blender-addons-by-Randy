SIDEBAR_CATEGORY = "Character Designer"

UI_PAGE_HAIR = "HAIR"
UI_PAGE_WEIGHT = "WEIGHT"
UI_PAGE_RIG = "RIG"
UI_PAGE_MODELING = "MODELING"
UI_PAGE_REFERENCE = "REFERENCE"
UI_PAGE_CLOTHING = "CLOTHING"
UI_PAGE_DEFAULT = UI_PAGE_HAIR

UI_PAGE_ITEMS = (
    (UI_PAGE_HAIR, "Hair", "Hair centerlines, curve recovery, and strand bones"),
    (UI_PAGE_WEIGHT, "Weight", "Automatic weighting and topology-aware Weight Flow"),
    (UI_PAGE_RIG, "Rig", "Limb IK, Spline IK, and rig controls"),
    (UI_PAGE_MODELING, "Modeling", "Topology-aware modeling helpers"),
    (UI_PAGE_REFERENCE, "Reference", "Generic reference view sets"),
    (UI_PAGE_CLOTHING, "Clothing", "Skirt controls, cloth physics, and animation baking"),
)
UI_PAGES = frozenset(item[0] for item in UI_PAGE_ITEMS)


def active_ui_page(context):
    """Return a valid session-only CDesigner page without mutating the Scene."""

    window_manager = getattr(context, "window_manager", None)
    settings = getattr(window_manager, "character_designer", None)
    page = getattr(settings, "ui_page", UI_PAGE_DEFAULT) if settings else UI_PAGE_DEFAULT
    return page if page in UI_PAGES else UI_PAGE_DEFAULT
