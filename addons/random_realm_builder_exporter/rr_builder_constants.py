import math
import os
import re

from mathutils import Vector


LEGACY_OUTPUT_ROOT = r"D:\Blender\~Import-Export\BuilderToUnity"
UNITY_PROJECT_ROOT = r"D:\Unity Projects\RandomRealm2"
UNITY_TEMP_OUTPUT_ROOT = r"D:\Unity Projects\RandomRealm2\Assets\~Temp\BlenderBridge"
UNITY_GENERATED_BUILD_ART_ROOT = r"D:\Unity Projects\RandomRealm2\Assets\Art\Generated\Builds"
UNITY_BUILDER_CACHE_ROOT = os.path.join(UNITY_PROJECT_ROOT, "Library", "RandomRealmBuilder")
UNITY_BUILDER_ICON_SOURCE_CACHE = os.path.join(UNITY_BUILDER_CACHE_ROOT, "IconSources")
UNITY_BUILDER_REFERENCE_INDEX = os.path.join(UNITY_BUILDER_CACHE_ROOT, "builder_reference_index.json")
UNITY_BUILDER_IMPORT_REQUEST = os.path.join(
    UNITY_PROJECT_ROOT,
    "Temp",
    "ImportSelectedBuilderGeneratedAssets.request",
)
DEFAULT_OUTPUT_ROOT = UNITY_TEMP_OUTPUT_ROOT
SUPPORTED_TYPES = ("Floor", "InnerWall", "Wall", "Stair", "Ramp", "Cube", "Door")
PROP_EXPORT_TYPES = {"Prop", "Props", "Furniture", "Decor", "Decoration", "Misc", "Stuff", "Table", "Chair"}
ICON_SIZES = (128, 256, 512, 1024, 2048)
PBR_BAKE_SIZES = (512, 1024, 2048, 4096)
ICON_PREVIEW_CAMERA_NAME = "RR_IconPreviewCamera"
ICON_PREVIEW_KEY_LIGHT_NAME = "RR_IconPreviewKeyLight"
ICON_PREVIEW_FILL_LIGHT_NAME = "RR_IconPreviewFillLight"
ICON_PREVIEW_BACK_LIGHT_NAME = "RR_IconPreviewBackLight"
ICON_PREVIEW_LIGHT_COLLECTION_NAME = "RR_IconPreview_Lights"
ICON_PREVIEW_LIGHT_NAME = ICON_PREVIEW_KEY_LIGHT_NAME
LEGACY_ICON_PREVIEW_LIGHT_NAMES = ("RR_IconPreviewSpotlight", "RR_IconPreviewLight")
ICON_PREVIEW_LIGHT_NAMES = (
    ICON_PREVIEW_KEY_LIGHT_NAME,
    ICON_PREVIEW_FILL_LIGHT_NAME,
    ICON_PREVIEW_BACK_LIGHT_NAME,
)
ICON_VIEW_DIR = Vector((4.0, -6.0, 4.0)).normalized()
ICON_CAMERA_DISTANCE = 3.0
ICON_CAMERA_FOV_DEGREES = 45.0
ICON_SPOTLIGHT_OFFSET = Vector((-3.0, -4.0, 5.0)).normalized()
ICON_LIGHT_BASE_ENERGY = 1500.0
ICON_LIGHT_BRIGHTNESS_DEFAULT = 1.5
ICON_LIGHT_BRIGHTNESS_MIN = 0.1
ICON_LIGHT_BRIGHTNESS_SOFT_MAX = 10.0
# Backward-compatible import alias. This is the recommended slider maximum,
# not a hard cap for values entered directly.
ICON_LIGHT_BRIGHTNESS_MAX = ICON_LIGHT_BRIGHTNESS_SOFT_MAX
ICON_LIGHT_RATIO_MIN = 0.0
ICON_LIGHT_RATIO_MAX = 2.0
ICON_KEY_LIGHT_RATIO_DEFAULT = 1.0
ICON_FILL_LIGHT_RATIO_DEFAULT = 650.0 / ICON_LIGHT_BASE_ENERGY
ICON_BACK_LIGHT_RATIO_DEFAULT = 900.0 / ICON_LIGHT_BASE_ENERGY
ICON_PREVIEW_LIGHT_SPECS = (
    {
        "name": ICON_PREVIEW_KEY_LIGHT_NAME,
        "label": "Key Light",
        "ratio_attr": "icon_key_light_ratio",
        "default_ratio": ICON_KEY_LIGHT_RATIO_DEFAULT,
        "legacy_names": LEGACY_ICON_PREVIEW_LIGHT_NAMES,
        "front": 0.75,
        "right": -0.45,
        "up": 0.75,
        "energy": 1500.0,
        "spot_size": math.radians(42.0),
        "spot_blend": 0.45,
        "shadow_soft_size": 0.16,
    },
    {
        "name": ICON_PREVIEW_FILL_LIGHT_NAME,
        "label": "Fill Light",
        "ratio_attr": "icon_fill_light_ratio",
        "default_ratio": ICON_FILL_LIGHT_RATIO_DEFAULT,
        "legacy_names": (),
        "front": 0.55,
        "right": 0.85,
        "up": 0.35,
        "energy": 650.0,
        "spot_size": math.radians(64.0),
        "spot_blend": 0.62,
        "shadow_soft_size": 0.22,
    },
    {
        "name": ICON_PREVIEW_BACK_LIGHT_NAME,
        "label": "Back Light",
        "ratio_attr": "icon_back_light_ratio",
        "default_ratio": ICON_BACK_LIGHT_RATIO_DEFAULT,
        "legacy_names": (),
        "front": -0.62,
        "right": 0.25,
        "up": 0.9,
        "energy": 900.0,
        "spot_size": math.radians(48.0),
        "spot_blend": 0.5,
        "shadow_soft_size": 0.14,
    },
)
ICON_BASE_SCALE = 1.55
ICON_ZOOM_MIN = 0.2
ICON_ZOOM_MAX = 5.0
ICON_PITCH_MIN = -80.0
ICON_PITCH_MAX = 80.0
TEXTURE_PACKAGE_DIR = os.path.join("textures", "_codex_packages")
TEXTURE_APPLIED_BACKUP_DIR = "_applied_backups"
TEXTURE_PACKAGE_APPLIED_FILENAME = "applied.json"
PBR_BAKE_OUTPUT_DIR = os.path.join("textures", "_baked_pbr")
PBR_BAKE_FRAME_LABEL = "RR Procedural Source"
PBR_BAKE_NODE_PREFIX = "RR_PBR_"
PBR_BAKE_MAPPING_FRAME_NAME = f"{PBR_BAKE_NODE_PREFIX}MappingFrame"
PBR_BAKE_TEXTURE_FRAME_NAME = f"{PBR_BAKE_NODE_PREFIX}TexturesFrame"
PBR_BAKE_TEXCOORD_NODE_NAME = f"{PBR_BAKE_NODE_PREFIX}TextureCoordinate"
PBR_BAKE_UVMAP_NODE_NAME = f"{PBR_BAKE_NODE_PREFIX}UVMap"
PBR_BAKE_MAPPING_NODE_NAME = f"{PBR_BAKE_NODE_PREFIX}Mapping"
LAYOUT_SNAPSHOT_MATRIX_PROP = "rr_layout_snapshot_matrix_world"
LAYOUT_SNAPSHOT_ROTATION_MODE_PROP = "rr_layout_snapshot_rotation_mode"
COLLIDER_HELPER_COLLECTION_NAME = "Builder_Hidden_Helpers"
COLLIDER_BOX_MATERIAL_NAME = "BuilderMat_Collider_Wire_Green"
OBJECT_MANAGER_ASSEMBLY_ROOT_PROP = "rr_object_manager_assembly_root"
OBJECT_MANAGER_ASSEMBLY_ID_PROP = "rr_object_manager_assembly_id"
OBJECT_MANAGER_ASSEMBLY_NAME_PROP = "rr_object_manager_assembly_name"
OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP = "rr_object_manager_assembly_root_name"
OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP = "rr_object_manager_parent_assembly_root_name"
OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP = "rr_object_manager_parent_assembly_id"
OBJECT_MANAGER_ASSEMBLY_CREATED_AT_PROP = "rr_object_manager_assembly_created_at"
OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP = "rr_object_manager_assembly_active_member"
OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP = "rr_object_manager_assembly_non_destructive"
OBJECT_MANAGER_ASSEMBLY_TYPE_PROP = "rr_object_manager_assembly_type"
OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP = "rr_variant_icon_source_member_name"
OBJECT_MANAGER_VARIANT_ICON_SOURCE_STABLE_ID_PROP = "rr_variant_icon_source_stable_id"
OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT = "ASSEMBLY"
OBJECT_MANAGER_ASSEMBLY_TYPE_ITEMS = (
    ("ASSEMBLY", "Assembly", "Separate parts that export as one logical asset"),
    ("VARIANTS", "Variants", "Alternative assets; queue/export one member and export the whole set"),
)
VARIANT_MEMBERSHIP_CONTRACT_VERSION = 1
VARIANT_MEMBERSHIP_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
VARIANT_PUBLISH_CLAIM_CONTRACT_VERSION = 1
VARIANT_PUBLISH_GATE_CONTRACT_VERSION = 1
VARIANT_READER_LEASE_CONTRACT_VERSION = 1
EXPORT_NAME_HINT_DISMISSED_PROP = "rr_export_name_hint_dismissed"
EXPORT_STABLE_ID_PROP = "rr_export_stable_id"
EXPORT_LAST_ID_PROP = "rr_export_last_id"
EXPORT_PREVIOUS_IDS_PROP = "rr_export_previous_ids"
EXPORT_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*_\d+x\d+x\d+$")


__all__ = [name for name in globals() if name.isupper()]
