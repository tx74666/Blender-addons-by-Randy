SIDEBAR_CATEGORY = "Character Designer"

UI_PAGE_HAIR = "HAIR"
UI_PAGE_WEIGHT = "WEIGHT"
UI_PAGE_RIG = "RIG"
UI_PAGE_MISC = "MISCELLANEOUS"
UI_PAGE_CLOTHING = "CLOTHING"
UI_PAGE_ANIMATION = "ANIMATION"
UI_PAGE_DEFAULT = UI_PAGE_HAIR

UI_PAGE_ITEMS = (
    # Keep the existing RNA values when combining the Modeling/Reference pages.
    (UI_PAGE_HAIR, "Hair", "Hair modeling, centerlines, and curve recovery", 0),
    (UI_PAGE_WEIGHT, "Weight", "Automatic weighting and weight symmetry", 1),
    (UI_PAGE_RIG, "Rig", "Body, hair, and skirt rigs and attachment", 2),
    (UI_PAGE_CLOTHING, "Clothing", "Legacy shortcut to Rig / Skirt", 5),
    (UI_PAGE_ANIMATION, "Animation", "Free local motion generation and body Actions", 6),
    (UI_PAGE_MISC, "Miscellaneous", "Modeling symmetry and reference view sets", 3),
)
UI_PAGES = frozenset(item[0] for item in UI_PAGE_ITEMS)

UI_RIG_SECTION_ITEMS = (
    ('BODY', 'Body', 'Body controls, Limb IK, Spline IK, and calibration'),
    ('HAIR', 'Hair', 'Hair strand bones and attachment to the character'),
    ('SKIRT', 'Skirt', 'Skirt controls, attachment, and physics'),
)


def active_ui_page(context):
    """Return a valid session-only CDesigner page without mutating the Scene."""

    window_manager = getattr(context, "window_manager", None)
    settings = getattr(window_manager, "character_designer", None)
    page = getattr(settings, "ui_page", UI_PAGE_DEFAULT) if settings else UI_PAGE_DEFAULT
    if page == UI_PAGE_CLOTHING:
        return UI_PAGE_RIG
    return page if page in UI_PAGES else UI_PAGE_DEFAULT


def active_rig_section(context):
    window_manager = getattr(context, 'window_manager', None)
    settings = getattr(window_manager, 'character_designer', None)
    if getattr(settings, 'ui_page', None) == UI_PAGE_CLOTHING:
        return 'SKIRT'
    section = getattr(settings, 'rig_section', 'BODY')
    return section if section in {'BODY', 'HAIR', 'SKIRT'} else 'BODY'


def rig_page_active(context, section='BODY'):
    return active_ui_page(context) == UI_PAGE_RIG and active_rig_section(context) == section
