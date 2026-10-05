"""Explicit, reviewable repair entry points for ordinary Unity export identities."""

import json
import os
import re
import sys
import textwrap

import bpy


def _main():
    return sys.modules[__package__ or "random_realm_builder_exporter"]


def _target(context, name="", uid=""):
    rr = _main()
    if name:
        root = bpy.data.objects.get(name)
    else:
        root = context.view_layer.objects.active
        if root is None:
            item = rr.get_active_queue_item(context.scene.rr_builder_export_settings)
            root = rr.queue_item_object(item) if item else rr.get_reference_object(context.scene)
    if root is None or root.type not in {"MESH", "EMPTY"}:
        raise ValueError("Select the Blender mesh that should update the Unity asset.")
    if uid and str(rr.object_manager_runtime_object_uid(root)) != uid:
        raise ValueError("The Blender object changed; start the diagnosis again.")
    return root


def _wrapped(layout, message, width=82):
    for line in textwrap.wrap(str(message), width=width) or [""]:
        layout.label(text=line)


def _keep_identity_owner_available(root):
    """Offer the explicit ownership choice only for independent copy conflicts."""
    rr = _main()
    if (root is None or root.library is not None or not root.is_editable
            or rr.export_identity_repair_is_group(root)):
        return False
    stable_id = str(root.get(rr.EXPORT_STABLE_ID_PROP, "") or "").strip().casefold()
    return bool(stable_id) and any(
        obj is not root
        and str(obj.get(rr.EXPORT_STABLE_ID_PROP, "") or "").strip().casefold() == stable_id
        for obj in bpy.data.objects
    )


class RR_OT_keep_export_identity_owner(bpy.types.Operator):
    bl_idname = "rr_builder.keep_export_identity_owner"
    bl_label = "Keep This Object's Identity"
    bl_description = "Keep the original object's export identity and give its copies independent identities"
    bl_options = {"REGISTER", "UNDO"}
    target_name: bpy.props.StringProperty(options={"HIDDEN"})
    target_uid: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        rr = _main()
        try:
            if not self.target_uid:
                raise ValueError("Start the identity diagnosis again to confirm this Blender object.")
            root = _target(context, self.target_name, self.target_uid)
            if not _keep_identity_owner_available(root):
                raise ValueError("Choose an editable independent original object whose export identity is shared by copies.")
            peers = rr.keep_export_identity_owner(root, context.scene.rr_builder_export_settings)
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Kept {root.name}'s identity; {len(peers)} copies now have independent identities. Names and geometry are unchanged.")
        return {"FINISHED"}


class RR_OT_open_unity_export_folder(bpy.types.Operator):
    bl_idname = "rr_builder.open_unity_export_folder"
    bl_label = "Open Unity Export Folder"
    bl_description = "Open the current Unity asset export directory without changing files"

    def execute(self, context):
        rr = _main()
        path = bpy.path.abspath(context.scene.rr_builder_export_settings.output_root)
        if not os.path.isdir(path):
            self.report({"ERROR"}, "Export folder does not exist. Set the output folder first.")
            return {"CANCELLED"}
        try:
            rr.rr_helper_open_path(path)
        except OSError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class RR_OT_copy_export_diagnostics(bpy.types.Operator):
    bl_idname = "rr_builder.copy_export_diagnostics"
    bl_label = "Copy Export Diagnostics"
    bl_description = "Copy readable export details and identity conflicts for debugging"
    target_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        rr = _main()
        try:
            root = _target(context, self.target_name)
            conflicts = rr.collect_export_identity_conflicts(root)
            settings = context.scene.rr_builder_export_settings
            context.window_manager.clipboard = "\n".join([
                "RR Helper " + ".".join(map(str, rr.bl_info["version"])),
                "Blend: " + bpy.data.filepath, "Object: " + root.name,
                "Export folder: " + bpy.path.abspath(settings.output_root),
                "Asset folder: " + rr.export_asset_id(root),
                "Stable identity: " + str(root.get(rr.EXPORT_STABLE_ID_PROP, "")),
                "Previous names: " + json.dumps(rr.export_previous_ids(root), ensure_ascii=False),
                "Conflicts:", *(conflicts or ["No export identity conflicts found."]),
            ])
        except (ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Export diagnostics copied.")
        return {"FINISHED"}


class RR_OT_export_identity_debug(bpy.types.Operator):
    bl_idname = "rr_builder.export_identity_debug"
    bl_label = "Export Diagnostics / Relink"
    bl_description = "Inspect conflicts and locate the existing Unity asset to pair with this object"
    target_name: bpy.props.StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        rr = _main()
        try:
            self._root = _target(context, self.target_name)
            self.target_name = self._root.name
            self._uid = str(rr.object_manager_runtime_object_uid(self._root))
            self._conflicts = rr.collect_export_identity_conflicts(self._root)
        except (ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        rr = _main()
        layout = self.layout
        try:
            self._conflicts = rr.collect_export_identity_conflicts(self._root)
        except (ValueError, RuntimeError, ReferenceError) as exc:
            _wrapped(layout, str(exc))
            return
        layout.label(text="Blender object: " + self.target_name, icon="OBJECT_DATA")
        layout.label(text="Unity asset folder: " + rr.export_asset_id(self._root))
        if self._conflicts:
            box = layout.box()
            box.label(text=f"{len(self._conflicts)} identity conflicts found", icon="ERROR")
            for conflict in self._conflicts:
                _wrapped(box, re.sub(r"stable ID '[^']*'", "Unity asset identity", conflict))
        else:
            layout.label(text="No export identity conflicts found.", icon="CHECKMARK")
        if _keep_identity_owner_available(self._root):
            owner_box = layout.box()
            owner_row = owner_box.row()
            owner_row.operator_context = "EXEC_DEFAULT"
            keep = owner_row.operator("rr_builder.keep_export_identity_owner", text="Keep This Object's Identity", icon="CHECKMARK")
            keep.target_name, keep.target_uid = self.target_name, self._uid
            _wrapped(owner_box, "Select the original object to keep its existing identity. Copies get independent export identities.")
        _wrapped(layout, "Select the object that should update the Unity asset, then choose its existing asset folder.")
        row = layout.row(align=True)
        row.operator("rr_builder.open_unity_export_folder", text="Open Export Folder", icon="FILE_FOLDER")
        copy = row.operator("rr_builder.copy_export_diagnostics", text="Copy Diagnostics", icon="COPYDOWN")
        copy.target_name = self.target_name
        settings = context.scene.rr_builder_export_settings
        choose_row = layout.row()
        choose_row.enabled = rr.export_mode_is_standard(settings) and not rr.export_identity_repair_is_group(self._root)
        choose = choose_row.operator("rr_builder.choose_unity_export_asset", text="Choose Unity Asset Folder...", icon="LINKED")
        choose.target_name, choose.target_uid = self.target_name, self._uid
        if not choose_row.enabled:
            _wrapped(layout, "Relink supports Standard independent assets. Managed groups use their group workflow.")
        if self._root.get(rr.EXPORT_ASSET_ID_OVERRIDE_PROP):
            reset = layout.operator("rr_builder.use_object_export_name", text="Use Object Name for Export", icon="X")
            reset.target_name = self.target_name

    def execute(self, context):
        return {"FINISHED"}


class RR_OT_choose_unity_export_asset(bpy.types.Operator):
    bl_idname = "rr_builder.choose_unity_export_asset"
    bl_label = "Choose Unity Asset Folder"
    bl_description = "Choose the asset's named folder inside the current Unity export directory"
    directory: bpy.props.StringProperty(name="Asset Folder", subtype="DIR_PATH")
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN"})
    target_name: bpy.props.StringProperty(options={"HIDDEN"})
    target_uid: bpy.props.StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        try:
            root = _target(context, self.target_name, self.target_uid)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.target_name = root.name
        self.target_uid = str(_main().object_manager_runtime_object_uid(root))
        self.directory = bpy.path.abspath(context.scene.rr_builder_export_settings.output_root).rstrip("/\\") + os.sep
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            _target(context, self.target_name, self.target_uid)
            result = bpy.ops.rr_builder.rebind_unity_asset(
                "INVOKE_DEFAULT", target_name=self.target_name, target_uid=self.target_uid,
                package_path=self.directory,
            )
            return {"CANCELLED"} if "CANCELLED" in result else {"FINISHED"}
        except (ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class RR_OT_rebind_unity_asset(bpy.types.Operator):
    bl_idname = "rr_builder.rebind_unity_asset"
    bl_label = "Confirm Unity Asset Pairing"
    bl_options = {"REGISTER", "UNDO"}
    target_name: bpy.props.StringProperty(options={"HIDDEN"})
    target_uid: bpy.props.StringProperty(options={"HIDDEN"})
    package_path: bpy.props.StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    detach_copies: bpy.props.BoolProperty(
        name="Give the listed copies independent export identities", default=False,
        description="Only the explicitly listed copies change; their names and geometry are preserved",
    )

    def invoke(self, context, event):
        rr = _main()
        try:
            root = _target(context, self.target_name, self.target_uid)
            self._plan = rr.build_unity_asset_rebind_plan(
                root, self.package_path, settings=context.scene.rr_builder_export_settings, detach_copies=True,
            )
            self._copies = [change["label"] for change in self._plan["changes"] if change["kind"] == "independent_copy"]
        except (ValueError, RuntimeError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            rr.show_builder_popup(context, str(exc), title="Pairing Needs Attention", icon="ERROR")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        layout = self.layout
        package = self._plan["package"]
        layout.label(text="Blender object: " + self._plan["target"].name, icon="OBJECT_DATA")
        layout.label(text="Unity asset: " + package["asset_id"], icon="LINKED")
        _wrapped(layout, "Folder: " + package["package_dir"])
        if package["source_object"]:
            layout.label(text="Previous source object: " + package["source_object"])
        if self._copies:
            box = layout.box()
            box.label(text="These copies currently share this asset's identity:", icon="ERROR")
            for name in self._copies:
                box.label(text=name, icon="OBJECT_DATA")
            box.prop(self, "detach_copies")
        _wrapped(layout, "Confirm updates the export association. Export again to publish the edited model to Unity.")

    def execute(self, context):
        rr = _main()
        try:
            root = _target(context, self.target_name, self.target_uid)
            settings = context.scene.rr_builder_export_settings
            plan = getattr(self, "_plan", None)
            if plan is None:
                plan = rr.build_unity_asset_rebind_plan(root, self.package_path, settings=settings, detach_copies=self.detach_copies)
            if any(change["kind"] == "independent_copy" for change in plan["changes"]) and not self.detach_copies:
                raise ValueError("Approve independent identities for the listed copies, or cancel this pairing.")
            rr.apply_unity_asset_rebind_plan(plan, settings)
        except (ValueError, RuntimeError, OSError, ReferenceError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Linked {root.name} to {plan['package']['asset_id']}. Export again to update Unity.")
        return {"FINISHED"}


class RR_OT_use_object_export_name(bpy.types.Operator):
    bl_idname = "rr_builder.use_object_export_name"
    bl_label = "Use Object Name for Export"
    bl_options = {"REGISTER", "UNDO"}
    target_name: bpy.props.StringProperty(options={"HIDDEN"})
    target_uid: bpy.props.StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        try:
            self._root = _target(context, self.target_name, self.target_uid)
            self.target_name = self._root.name
            self.target_uid = str(_main().object_manager_runtime_object_uid(self._root))
            self._preview_identity = {key: self._root.get(key) for key in (
                _main().EXPORT_STABLE_ID_PROP, _main().EXPORT_LAST_ID_PROP,
                _main().EXPORT_PREVIOUS_IDS_PROP, _main().EXPORT_ASSET_ID_OVERRIDE_PROP,
                _main().EXPORT_FOLLOW_NAME_PROP,
            )}
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        rr = _main()
        self.layout.label(text="Current asset: " + rr.export_asset_id(self._root))
        self.layout.label(text="New asset name: " + rr.object_manager_display_name(self._root))
        _wrapped(self.layout, "The asset identity and previous name remain tracked for the next export.")

    def execute(self, context):
        rr = _main()
        try:
            root = _target(context, self.target_name, self.target_uid)
            if root.library is not None or not root.is_editable or rr.export_identity_repair_is_group(root):
                raise ValueError("Choose an editable independent object.")
            preview = getattr(self, "_preview_identity", None)
            if preview is not None and any(root.get(key) != value for key, value in preview.items()):
                raise ValueError("Export identity changed since the preview; inspect it again.")
            with rr.rr_export_identity_tracking.transaction([root]):
                rr.snapshot_export_identity(root)
                if rr.EXPORT_ASSET_ID_OVERRIDE_PROP in root:
                    del root[rr.EXPORT_ASSET_ID_OVERRIDE_PROP]
                root[rr.EXPORT_FOLLOW_NAME_PROP] = True
                rr.ensure_export_identity(root)
                rr.validate_export_identity(root)
            rr.remember_object_manager_name_sync_state(root)
            rr.tag_rr_addon_view3d_redraw()
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


IDENTITY_REPAIR_CLASSES = (
    RR_OT_open_unity_export_folder, RR_OT_copy_export_diagnostics,
    RR_OT_export_identity_debug, RR_OT_keep_export_identity_owner,
    RR_OT_choose_unity_export_asset,
    RR_OT_rebind_unity_asset, RR_OT_use_object_export_name,
)
