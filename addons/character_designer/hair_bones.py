"""Small Hair-page workflow for strand discovery and native bone control."""

import hashlib
import importlib

import bpy
from bpy.props import EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from .hair_bones_topology import HairTopologyError, select_strands, selected_strands
from .hair_bones_rig import HairBonesRigError, build_hair_bones
from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_HAIR, active_ui_page


def _groups():
    return importlib.import_module(__package__ + ".hair_bones_groups")


def _variants():
    return importlib.import_module(__package__ + ".hair_bones_variants")


def _source(context):
    obj = context.active_object
    if obj:
        source = _variants().source_for(obj)
        if source:
            return source
        try:
            source = _groups().source_from_context(context)
            if source:
                return source
        except ValueError:
            pass
    settings = _settings(context)
    return settings.source if settings else None


_ENUM_ITEMS = {}
_ENUM_STRINGS = {}


def _enum_item(identifier, label, description=""):
    # Blender retains dynamic-enum strings. Hold their storage for the module's
    # RNA lifetime, and keep numeric values stable when groups are reordered.
    values = tuple(_ENUM_STRINGS.setdefault(value, value) for value in (identifier, label, description))
    number = 0 if identifier == "NONE" else int(hashlib.sha256(identifier.encode()).hexdigest()[:7], 16) + 1
    return (*values, 0, number)


def _group_items(self, context):
    source = _source(context) if context else self.source
    try:
        entries = _groups().read_groups(source) if source else ()
        items = [_enum_item(entry["id"], f'{index + 1}. {entry["name"]} ({len(entry["members"])} strands)')
                 for index, entry in enumerate(entries)]
    except (ValueError, RuntimeError):
        items = []
    _ENUM_ITEMS["groups"] = [_enum_item("NONE", "Choose Group" if items else "No groups captured")] + items
    return _ENUM_ITEMS["groups"]


def _variant_items(self, context):
    source = _source(context) if context else self.source
    collections = _variants().variants_for(source) if source else ()
    _ENUM_ITEMS["variants"] = [_enum_item("NONE", "Choose Version" if collections else "No generated versions")]
    _ENUM_ITEMS["variants"].extend(_enum_item(collection.name, collection.name, "Show this independent result")
                                   for collection in collections)
    return _ENUM_ITEMS["variants"]


def _settings(context):
    return getattr(context.window_manager, "character_designer_hair_bones", None)


def _mesh_edit(context):
    obj = context.active_object
    return obj is not None and obj.type == "MESH" and context.mode == "EDIT_MESH"


def _source_edit(context):
    return _mesh_edit(context) and _variants().source_for(context.active_object) is context.active_object


def _report(operator, context, message, *, error=False):
    settings = _settings(context)
    if settings:
        settings.last_message = message
    operator.report({"ERROR" if error else "INFO"}, message)


class CharacterDesignerHairBonesState(PropertyGroup):
    source: PointerProperty(type=bpy.types.Object, name="Source Mesh", options={"SKIP_SAVE"})
    mode: EnumProperty(
        name="Generate", items=(("PER_STRAND", "Per Strand", "One chain per captured hair strand"),
                                ("GROUPED", "Grouped", "One shared chain per saved group")),
        default="PER_STRAND",
    )
    active_group: EnumProperty(name="Group", items=_group_items)
    active_variant: EnumProperty(name="Version", items=_variant_items)
    bone_count: IntProperty(
        name="Bones per Chain",
        description="Number of bones per strand or shared group chain",
        default=4,
        min=1,
        max=12,
    )
    last_message: StringProperty(options={"SKIP_SAVE"})


class CHARACTERDESIGNER_OT_select_hair_strands(Operator):
    bl_idname = "character_designer.select_hair_strands"
    bl_label = "Select Hair Strands"
    bl_description = (
        "Find long hair strips up to their root junctions. Selected vertices limit "
        "the search; deselect all to search the visible mesh"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _source_edit(context)

    def execute(self, context):
        try:
            obj, plans = select_strands(context)
            records = _groups().capture_plans(obj, plans)
            _settings(context).source = obj
            if records:
                _settings(context).active_group = records[0]["id"]
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        count = len(plans)
        _report(self, context, f"Captured {count} hair strand{'s' if count != 1 else ''}. Choose Per Strand or Grouped, then Generate New Version.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_group_selected(Operator):
    bl_idname = "character_designer.hair_group_selected"
    bl_label = "Group Selected Strands"
    bl_description = "Move the selected captured strands into one group without binding them"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _source_edit(context)

    def execute(self, context):
        try:
            group_id = _groups().group_selected(context)
            settings = _settings(context)
            settings.source = context.active_object
            settings.active_group = group_id
            settings.mode = "GROUPED"
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, "Selected strands now share one group. Generate a new version to compare.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_split_selected(Operator):
    bl_idname = "character_designer.hair_split_selected"
    bl_label = "Split Selected Strands"
    bl_description = "Give each selected strand its own group; keep the remaining groups"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _source_edit(context)

    def execute(self, context):
        try:
            _groups().split_selected(context)
            source = context.active_object
            _settings(context).source = source
            records = _groups().read_groups(source)
            if records:
                _settings(context).active_group = records[0]["id"]
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, "Selected strands now have individual groups.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_select_group(Operator):
    bl_idname = "character_designer.hair_select_group"
    bl_label = "Select Group"
    bl_description = "Highlight this group's source strands"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            source = _source(context)
            group_id = _settings(context).active_group
            _variants().show_source(context, source)
            _groups().select_group(context, group_id)
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_edit_guide(Operator):
    bl_idname = "character_designer.hair_edit_guide"
    bl_label = "Edit Group Guide"
    bl_description = "Create or select this group's editable guide; generate a new version after editing"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        state = visibility = None
        source = guide = existing = None
        old_metadata = old_guide_visibility = None
        try:
            source = _source(context)
            settings = _settings(context)
            group_id = settings.active_group
            entry = next((record for record in _groups().read_groups(source)
                          if record["id"] == group_id), None)
            if entry is None:
                raise ValueError("Choose a captured group before editing its guide.")
            existing = entry["guide"]
            if existing and (existing.library or existing.data.library or existing.override_library):
                raise ValueError("Make the group guide local and editable before editing it.")
            if existing and not _groups().guide_in_editable_collection(context, existing):
                raise ValueError("Reveal and unlock the guide's collection before editing it.")
            state = _variants()._context_snapshot(context, source, None)
            visibility = _variants()._visibility_snapshot(source)
            old_metadata = source.get(_groups().GROUPS_KEY)
            if existing:
                old_guide_visibility = _variants()._visibility(existing)
            _variants().show_source(context, source)
            guide = _groups().create_group_guide(context, group_id,
                                                point_count=settings.bone_count + 1)
            bpy.ops.object.mode_set(mode="OBJECT")
            for obj in context.selected_objects:
                obj.select_set(False)
            if context.view_layer.objects.get(guide.name) is not guide:
                raise ValueError("Reveal the guide's collection in this View Layer before editing it.")
            guide.hide_viewport = False
            guide.hide_select = False
            guide.hide_set(False)
            guide.select_set(True)
            context.view_layer.objects.active = guide
            bpy.ops.object.mode_set(mode="EDIT")
        except (ValueError, RuntimeError) as exc:
            if state is not None:
                try:
                    if guide is not None and existing is None:
                        if context.object and context.object.mode != "OBJECT":
                            bpy.ops.object.mode_set(mode="OBJECT")
                        data = guide.data
                        bpy.data.objects.remove(guide, do_unlink=True)
                        if data.users == 0:
                            bpy.data.curves.remove(data)
                        source[_groups().GROUPS_KEY] = old_metadata
                    if existing and old_guide_visibility is not None:
                        _variants()._set_visibility(existing, old_guide_visibility)
                    _variants()._restore_visibility(visibility)
                    context.view_layer.update()
                    _variants()._restore_context(context, source, state)
                except Exception as restore_error:
                    _report(self, context, f"{exc} Could not restore the editor: {restore_error}", error=True)
                    return {"CANCELLED"}
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, "Edit the guide points, then Generate New Version. Previous results are preserved.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_build_version(Operator):
    bl_idname = "character_designer.hair_build_version"
    bl_label = "Generate New Version"
    bl_description = "Build all captured strands with both Mirror sides and one chain per central strand; preserve previous versions"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _source(context) is not None

    def execute(self, context):
        try:
            settings = _settings(context)
            source = _source(context)
            settings.source = source
            # The source may be hidden while a previous result is in Pose Mode.
            obj, plans = _groups().build_plans(context, mode=settings.mode, source=source)
            result = _variants().build_variant(context, obj, plans, mode=settings.mode,
                                               bone_count=settings.bone_count)
            settings.active_variant = result["collection"].name
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, f'Created {len(result["chains"])} chains in {result["collection"].name}.')
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_show_source(Operator):
    bl_idname = "character_designer.hair_show_source"
    bl_label = "Edit Source / Groups"
    bl_description = "Return to the original hair mesh and hide generated versions"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            _variants().show_source(context, _source(context))
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_show_version(Operator):
    bl_idname = "character_designer.hair_show_version"
    bl_label = "Show Version"
    bl_description = "Show the chosen result and select its controls"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            collection = bpy.data.collections.get(_settings(context).active_variant)
            _variants().show_variant(context, collection)
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_clear_groups(Operator):
    bl_idname = "character_designer.hair_clear_groups"
    bl_label = "Recapture Strands"
    bl_description = "Clear the saved strand selection and groups; keep existing guides and generated versions"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            source = _source(context)
            _variants().show_source(context, source)
            _groups().clear_groups(source)
            bpy.ops.mesh.select_all(action="DESELECT")
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, "Choose the new source strands, then Select Hair Strands to capture them.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_generate_hair_bones(Operator):
    bl_idname = "character_designer.generate_hair_bones"
    bl_label = "Generate Hair Bones"
    bl_description = (
        "Create and bind a connected bone chain for each selected hair strand, "
        "then select the controls in Pose Mode"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _mesh_edit(context) and _settings(context) is not None

    def execute(self, context):
        try:
            obj, plans = selected_strands(context)
            result = build_hair_bones(
                context, obj, plans, bone_count=_settings(context).bone_count,
            )
        except (HairTopologyError, HairBonesRigError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        count = len(result["chains"])
        action = "Created" if result["created"] else "Selected"
        _report(self, context, f"{action} {count} hair chain{'s' if count != 1 else ''}. Rotate the selected bones in Pose Mode.")
        return {"FINISHED"}


class CHARACTERDESIGNER_PT_hair_bones(Panel):
    bl_label = "Hair Bones"
    bl_idname = "CHARACTERDESIGNER_PT_hair_bones"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_HAIR

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        if settings is None:
            return
        source = _source(context)
        if source:
            layout.label(text=f"Source: {source.name}", icon="OUTLINER_OB_MESH")
        layout.prop(settings, "mode", expand=True)
        layout.operator("character_designer.select_hair_strands", icon="RESTRICT_SELECT_OFF")
        try:
            records = _groups().read_groups(source) if source else ()
        except (ValueError, RuntimeError) as exc:
            layout.label(text=str(exc), icon="ERROR")
            records = ()
        if records:
            strands = sum(len(record["members"]) for record in records)
            chains = strands if settings.mode == "PER_STRAND" else len(records)
            if any(mod.type == "MIRROR" for mod in source.modifiers):
                layout.label(text=f"{strands} captured strands")
                layout.label(text="Mirror: both sides + center", icon="MOD_MIRROR")
            else:
                layout.label(text=f"{strands} strands / {chains} chains")
            if settings.mode == "GROUPED":
                row = layout.row(align=True)
                row.operator("character_designer.hair_group_selected", text="Group Selected")
                row.operator("character_designer.hair_split_selected", text="Split Selected")
                layout.prop(settings, "active_group")
                row = layout.row(align=True)
                row.operator("character_designer.hair_select_group", text="Select Group")
                row.operator("character_designer.hair_edit_guide", text="Edit Guide", icon="CURVE_DATA")
            layout.prop(settings, "bone_count")
            layout.operator("character_designer.hair_build_version", icon="BONE_DATA")
        if source and _variants().variants_for(source):
            layout.prop(settings, "active_variant")
            row = layout.row(align=True)
            row.operator("character_designer.hair_show_version", text="Show Version")
            row.operator("character_designer.hair_show_source", text="Edit Source")
            active = context.active_object
            if active and active.get(_variants().SOURCE_KEY) is source:
                collection = next((item for item in active.users_collection
                                   if item.get(_variants().VERSION_KEY) == 1), None)
                mesh = collection.get(_variants().MESH_KEY) if collection else None
                try:
                    record = _variants().rig._read_records(mesh) if mesh else None
                except ValueError:
                    record = None
                if record:
                    layout.label(text=f'{len(record["chains"])} generated chains')
                    if record.get("mirrors") and record.get("mirror_layout") != "BEFORE_ARMATURE_X":
                        layout.label(text="Old version: half-side controls", icon="INFO")
                        layout.label(text="Generate a new version to update.")
        elif source and context.active_object != source:
            layout.operator("character_designer.hair_show_source", text="Edit Source", icon="EDITMODE_HLT")
        if source is not None and _groups().GROUPS_KEY in source:
            layout.operator("character_designer.hair_clear_groups", text="Recapture Strands", icon="FILE_REFRESH")
        if _source_edit(context):
            layout.label(text="Select a tip to limit the search.", icon="INFO")
            layout.label(text="Deselect all to find visible strands.")
        elif context.mode == "POSE":
            layout.label(text="Rotate hair bones in Pose Mode.", icon="INFO")
            layout.label(text="Mesh Edit Mode to add more strands.")
        else:
            layout.label(text="Select hair in Mesh Edit Mode.", icon="INFO")


HAIR_BONES_CLASSES = (
    CharacterDesignerHairBonesState,
    CHARACTERDESIGNER_OT_select_hair_strands,
    CHARACTERDESIGNER_OT_generate_hair_bones,
    CHARACTERDESIGNER_OT_hair_group_selected,
    CHARACTERDESIGNER_OT_hair_split_selected,
    CHARACTERDESIGNER_OT_hair_select_group,
    CHARACTERDESIGNER_OT_hair_edit_guide,
    CHARACTERDESIGNER_OT_hair_build_version,
    CHARACTERDESIGNER_OT_hair_show_source,
    CHARACTERDESIGNER_OT_hair_show_version,
    CHARACTERDESIGNER_OT_hair_clear_groups,
    CHARACTERDESIGNER_PT_hair_bones,
)
