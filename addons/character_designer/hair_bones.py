"""Hair-page workflow for independent chains on the original character mesh."""

import importlib

import bpy
from bpy.props import IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import hair_bones_rig as rig
from .hair_bones_topology import select_strands
from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_HAIR, active_ui_page


def _groups():
    return importlib.import_module(__package__ + ".hair_bones_groups")


def _variants():
    return importlib.import_module(__package__ + ".hair_bones_variants")


def _binding():
    return importlib.import_module(__package__ + ".hair_bones_binding")


def _source(context):
    obj = context.active_object
    if obj:
        if obj.type == "ARMATURE":
            pose_bone = getattr(context, "active_pose_bone", None)
            bone = pose_bone.bone if pose_bone else getattr(context, "active_bone", None)
            if bone and bone.get(rig.OWNER_KEY) == rig.OWNER_VALUE:
                source = bone.get(rig.SOURCE_KEY)
                if isinstance(source, bpy.types.Object) and source.type == "MESH":
                    return _variants().source_for(source)
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


def _settings(context):
    return getattr(context.window_manager, "character_designer_hair_bones", None)


def _mesh_edit(context):
    obj = context.active_object
    return obj is not None and obj.type == "MESH" and context.mode == "EDIT_MESH"


def _source_edit(context):
    return _mesh_edit(context) and _variants().source_for(context.active_object) is context.active_object


def _armature_poll(self, obj):
    return obj.type == "ARMATURE"


def _report(operator, context, message, *, error=False):
    settings = _settings(context)
    if settings:
        settings.last_message = message
    operator.report({"ERROR" if error else "INFO"}, message)


def _bind(operator, context):
    try:
        settings = _settings(context)
        source = _source(context)
        obj, plans = _groups().build_plans(context, source=source)
        result = _binding().bind_hair(context, obj, plans, bone_count=settings.bone_count,
                                      armature=settings.target_armature)
        settings.source = obj
    except (ValueError, RuntimeError) as exc:
        _report(operator, context, str(exc), error=True)
        return {"CANCELLED"}
    _report(operator, context,
            f'Bound {len(result["chains"])} independent hair chains to {result["armature"].name} / {result["parent_bone"]}.')
    return {"FINISHED"}


class CharacterDesignerHairBonesState(PropertyGroup):
    source: PointerProperty(type=bpy.types.Object, name="Source Mesh", options={"SKIP_SAVE"})
    target_armature: PointerProperty(
        type=bpy.types.Object, name="Character Rig", poll=_armature_poll,
        description="Character armature to receive the hair bones; leave empty to detect it automatically",
    )
    bone_count: IntProperty(
        name="Bones per Chain",
        description="Number of bones in each strand's independent chain",
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
            _groups().capture_plans(obj, plans)
            _settings(context).source = obj
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        count = len(plans)
        _report(self, context, f"Captured {count} hair strand{'s' if count != 1 else ''}. Bind Hair to Character to add their independent chains.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_bind_to_character(Operator):
    bl_idname = "character_designer.hair_bind_to_character"
    bl_label = "Bind Hair to Character"
    bl_description = "Bind the original hair mesh to independent chains parented to the character's head, including both Mirror sides and central strands"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _source(context) is not None and _settings(context) is not None

    def execute(self, context):
        return _bind(self, context)


class CHARACTERDESIGNER_OT_hair_remove_binding(Operator):
    bl_idname = "character_designer.hair_remove_binding"
    bl_label = "Remove Hair Binding"
    bl_description = "Remove this tool's hair binding and restore the original hair mesh's binding state"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        source = _source(context)
        return source is not None and _binding().is_bound(source)

    def execute(self, context):
        try:
            source = _source(context)
            result = _binding().remove_hair_binding(context, source)
            _settings(context).source = source
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        removed = result["removed_bones"]
        count = removed if isinstance(removed, int) else len(removed)
        _report(self, context, f"Removed {count} hair bones. The original hair mesh is preserved.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_cleanup_generated_copies(Operator):
    bl_idname = "character_designer.hair_cleanup_generated_copies"
    bl_label = "Cleanup Generated Copies"
    bl_description = "Delete this source's old generated hair copies and their dedicated rigs; keep the original mesh and character armature"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        source = _source(context)
        return source is not None and bool(_variants().variants_for(source))

    def execute(self, context):
        try:
            source = _source(context)
            result = _binding().remove_generated_copies(context, source)
            _settings(context).source = source
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        meshes = result["removed_meshes"]
        rigs = result["removed_rigs"]
        mesh_count = meshes if isinstance(meshes, int) else len(meshes)
        rig_count = rigs if isinstance(rigs, int) else len(rigs)
        _report(self, context, f"Removed {mesh_count} generated hair copies and {rig_count} dedicated rigs.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_show_source(Operator):
    bl_idname = "character_designer.hair_show_source"
    bl_label = "Edit Source"
    bl_description = "Return to the original hair mesh and hide old generated copies"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            source = _source(context)
            _variants().show_source(context, source)
            _settings(context).source = source
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_hair_clear_groups(Operator):
    bl_idname = "character_designer.hair_clear_groups"
    bl_label = "Recapture Strands"
    bl_description = "Clear the saved strand capture; keep the mesh, existing bones and guides"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            source = _source(context)
            _variants().show_source(context, source)
            _groups().clear_groups(source)
            _settings(context).source = source
            bpy.ops.mesh.select_all(action="DESELECT")
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, "Choose the new source strands, then Select Hair Strands to capture them.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_generate_hair_bones(Operator):
    """Redirect the old script entry point to the character binding workflow."""
    bl_idname = "character_designer.generate_hair_bones"
    bl_label = "Bind Hair to Character"
    bl_description = "Bind the original hair mesh to independent bone chains on the character armature"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _source(context) is not None and _settings(context) is not None

    def execute(self, context):
        return _bind(self, context)


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
        layout.label(text="One independent chain per strand.")
        layout.operator("character_designer.select_hair_strands", icon="RESTRICT_SELECT_OFF")
        try:
            strands = _groups().captured_strand_count(source) if source else 0
        except (ValueError, RuntimeError) as exc:
            layout.label(text=str(exc), icon="ERROR")
            strands = 0
        try:
            bound = _binding().is_bound(source) if source else False
        except (ValueError, RuntimeError) as exc:
            layout.label(text=str(exc), icon="ERROR")
            bound = False
        if source:
            layout.prop(settings, "target_armature")
            target = None
            try:
                target, head = _binding().resolve_target(context, source, armature=settings.target_armature)
                layout.label(text=f"Rig: {target.name}", icon="ARMATURE_DATA")
                layout.label(text=f"Head: {getattr(head, 'name', head)}", icon="BONE_DATA")
            except (ValueError, RuntimeError) as exc:
                layout.label(text=str(exc), icon="INFO")
            if strands:
                if any(mod.type == "MIRROR" for mod in source.modifiers):
                    layout.label(text=f"{strands} captured strands")
                    layout.label(text="Mirror: both sides + center", icon="MOD_MIRROR")
                else:
                    layout.label(text=f"{strands} strands / {strands} chains")
                layout.prop(settings, "bone_count")
                row = layout.row()
                row.enabled = target is not None and not bound
                row.operator("character_designer.hair_bind_to_character", icon="BONE_DATA")
            if bound:
                remove_row = layout.row()
                remove_row.alert = True
                remove_row.operator("character_designer.hair_remove_binding", icon="UNLINKED")
            if _variants().variants_for(source):
                cleanup_row = layout.row()
                cleanup_row.alert = True
                cleanup_row.operator("character_designer.hair_cleanup_generated_copies", icon="TRASH")
            if not _source_edit(context):
                layout.operator("character_designer.hair_show_source", text="Edit Source", icon="EDITMODE_HLT")
            if _groups().GROUPS_KEY in source:
                layout.operator("character_designer.hair_clear_groups", text="Recapture Strands", icon="FILE_REFRESH")
        if _source_edit(context):
            layout.label(text="Select a tip to limit the search.", icon="INFO")
            layout.label(text="Deselect all to find visible strands.")
        elif context.mode == "POSE":
            layout.label(text="Rotate hair bones in Pose Mode.", icon="INFO")
        else:
            layout.label(text="Select hair in Mesh Edit Mode.", icon="INFO")


HAIR_BONES_CLASSES = (
    CharacterDesignerHairBonesState,
    CHARACTERDESIGNER_OT_select_hair_strands,
    CHARACTERDESIGNER_OT_generate_hair_bones,
    CHARACTERDESIGNER_OT_hair_bind_to_character,
    CHARACTERDESIGNER_OT_hair_remove_binding,
    CHARACTERDESIGNER_OT_hair_cleanup_generated_copies,
    CHARACTERDESIGNER_OT_hair_show_source,
    CHARACTERDESIGNER_OT_hair_clear_groups,
    CHARACTERDESIGNER_PT_hair_bones,
)
