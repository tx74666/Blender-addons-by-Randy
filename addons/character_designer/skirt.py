"""Clothing-page workflow for a fitted skirt rig and continuous physics baking."""

import importlib
import json

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_CLOTHING, active_ui_page


_ACTIVE_BAKES = {}
LAST_BAKE_KEY = "character_designer_skirt_last_bake"
PREVIEW_KEY = "character_designer_skirt_bake_preview"


def _rig():
    return importlib.import_module(__package__ + ".skirt_rig")


def _physics():
    return importlib.import_module(__package__ + ".skirt_physics")


def _settings(context):
    return getattr(context.window_manager, "character_designer_skirt", None)


def _source(context):
    """Prefer the selected source/owned helper, then the last explicit source."""
    source = _rig().find_source(context)
    if source is not None:
        return source
    settings = _settings(context)
    return settings.source if settings is not None else None


def _record(context):
    source = _source(context)
    return _rig().read_record(source) if source is not None else None


def _report(operator, context, message, *, error=False):
    settings = _settings(context)
    if settings is not None:
        settings.last_message = message
    operator.report({"ERROR" if error else "INFO"}, message)


def _mesh_only(_self, obj):
    return obj.type == "MESH"


def _armature_only(_self, obj):
    return obj.type == "ARMATURE"


def _idle(context):
    return context.window_manager.as_pointer() not in _ACTIVE_BAKES


def _has_setup(context):
    try:
        return _record(context) is not None
    except (ValueError, RuntimeError, ReferenceError):
        return False


def _has_physics(context):
    try:
        return bool((_record(context) or {}).get("physics"))
    except (ValueError, RuntimeError, ReferenceError):
        return False


def _helper_objects(record, kind):
    physics = record.get("physics") or {}
    if kind == "COLLIDERS":
        names = physics.get("colliders", [])
    else:
        names = list(record.get("cage", []))
        if physics.get("proxy"):
            names.append(physics["proxy"])
    return [obj for name in names if (obj := bpy.data.objects.get(name)) is not None]


def _helpers_visible(record, kind, context):
    return any(not obj.hide_get(view_layer=context.view_layer)
               for obj in _helper_objects(record, kind)
               if obj.name in context.view_layer.objects)


def _bake_range(context):
    settings = _settings(context)
    if settings.use_scene_range:
        start, end = context.scene.frame_start, context.scene.frame_end
    else:
        start, end = settings.bake_start, settings.bake_end
    if end < start:
        raise ValueError("Bake End must be at or after Bake Start.")
    return start, end


def _last_bake(source):
    if source is None:
        return None
    try:
        result = json.loads(source.get(LAST_BAKE_KEY, "null"))
        if (result and bpy.data.objects.get(result.get("rig", ""))
                and bpy.data.objects.get(result.get("mesh", ""))
                and bpy.data.collections.get(result.get("collection", ""))):
            return result
    except (TypeError, ValueError, AttributeError):
        pass
    return None


def _remember_bake(source, result):
    if not result or not all(result.get(key) for key in ("rig", "mesh", "collection")):
        return
    source[LAST_BAKE_KEY] = json.dumps(result, ensure_ascii=False)
    for key in ("rig", "mesh"):
        obj = bpy.data.objects.get(result[key])
        if obj is not None:
            # An export can resolve its editing source without joining its ownership.
            obj[_rig().SOURCE_KEY] = source


def _restore_preview(context, source):
    raw = source.get(PREVIEW_KEY)
    if not raw:
        return
    try:
        snapshot = json.loads(raw)
        view_layer = context.scene.view_layers.get(snapshot["view_layer"])
        if view_layer is not None:
            for name, hidden in snapshot["objects"].items():
                obj = bpy.data.objects.get(name)
                if obj is not None and obj.name in view_layer.objects:
                    obj.hide_set(hidden, view_layer=view_layer)
        collection = bpy.data.collections.get(snapshot["collection"])
        if collection is not None:
            collection.hide_viewport = snapshot["collection_hidden"]
        del source[PREVIEW_KEY]
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("The saved skirt preview state cannot be read.") from exc


def _show_baked(context, source):
    result = _last_bake(source)
    if not result:
        raise ValueError("Bake an animation copy before inspecting it.")
    if PREVIEW_KEY in source:
        _restore_preview(context, source)
    record = _rig().read_record(source)
    collection = bpy.data.collections[result["collection"]]
    generated = [source]
    if record:
        generated.extend(obj for obj in bpy.data.objects
                         if obj.get(_rig().OWNER_KEY) == record["owner"])
    visible_objects = [obj for obj in generated if obj.name in context.view_layer.objects]
    baked_objects = [bpy.data.objects[result[key]] for key in ("rig", "mesh")]
    snapshot = {
        "view_layer": context.view_layer.name,
        "objects": {obj.name: obj.hide_get(view_layer=context.view_layer)
                    for obj in visible_objects + baked_objects
                    if obj.name in context.view_layer.objects},
        "collection": collection.name,
        "collection_hidden": collection.hide_viewport,
    }
    source[PREVIEW_KEY] = json.dumps(snapshot, ensure_ascii=False)
    try:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in visible_objects:
            obj.hide_set(True, view_layer=context.view_layer)
        collection.hide_viewport = False
        context.view_layer.update()
        for obj in baked_objects:
            if obj.name not in context.view_layer.objects:
                raise ValueError("The baked copy is excluded from this view layer.")
            obj.hide_set(False, view_layer=context.view_layer)
        baked_objects[1].select_set(True)
        context.view_layer.objects.active = baked_objects[1]
    except (ValueError, RuntimeError):
        _restore_preview(context, source)
        raise


class CharacterDesignerSkirtState(PropertyGroup):
    source: PointerProperty(type=bpy.types.Object, name="Skirt", poll=_mesh_only,
                            options={"SKIP_SAVE"})
    chain_count: IntProperty(name="Chains", description="Bone chains around the skirt",
                             default=8, min=3, max=32, options={"SKIP_SAVE"})
    segment_count: IntProperty(name="Bones per Chain", default=4, min=2, max=12,
                               options={"SKIP_SAVE"})
    physics: BoolProperty(name="Physics + Colliders", default=True, options={"SKIP_SAVE"},
                          description="Create a cloth proxy and closed character colliders")
    show_attachment: BoolProperty(name="Attachment Options", default=False,
                                  options={"SKIP_SAVE"})
    armature: PointerProperty(type=bpy.types.Object, name="Character Rig", poll=_armature_only,
                              options={"SKIP_SAVE"},
                              description="Optional override; otherwise detect the character rig")
    parent_bone: StringProperty(name="Pelvis Bone", options={"SKIP_SAVE"},
                                description="Optional override; otherwise detect the pelvis bone")
    use_scene_range: BoolProperty(name="Use Scene Frame Range", default=True,
                                  options={"SKIP_SAVE"})
    bake_start: IntProperty(name="Start", default=1, min=-1048574, max=1048574,
                            options={"SKIP_SAVE"})
    bake_end: IntProperty(name="End", default=250, min=-1048574, max=1048574,
                          options={"SKIP_SAVE"})
    last_message: StringProperty(options={"SKIP_SAVE"})


class CHARACTERDESIGNER_OT_create_skirt_setup(Operator):
    bl_idname = "character_designer.create_skirt_setup"
    bl_label = "Create Skirt Setup"
    bl_description = "Fit a tapered wire cage, waist ring, bone controls, weights, and optional physics"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _idle(context) and _settings(context) is not None and _source(context) is not None

    def execute(self, context):
        source = _source(context)
        settings = _settings(context)
        had_setup = False
        built = False
        try:
            _restore_preview(context, source)
            had_setup = _rig().read_record(source) is not None
            settings.source = source
            record = _rig().build_skirt(
                context, source, chain_count=settings.chain_count,
                segment_count=settings.segment_count, armature=settings.armature,
                parent_bone=settings.parent_bone,
            )
            built = True
            if settings.physics:
                _physics().add_physics(context, source)
            _rig().select_controls(context, source)
            record = _rig().read_record(source) or record
        except (ValueError, RuntimeError) as exc:
            # Existing artist work must survive a failed attempt to add physics.
            if built and not had_setup:
                try:
                    _rig().remove_skirt(context, source)
                except (ValueError, RuntimeError) as rollback_error:
                    _report(self, context, f"{exc} Cleanup: {rollback_error}", error=True)
                    return {"CANCELLED"}
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        count = record.get("chain_count", settings.chain_count)
        segments = record.get("segment_count", settings.segment_count)
        message = f"Skirt ready: {count} chains × {segments} bones. Pose the waist and wire controls."
        _report(self, context, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_skirt_select_controls(Operator):
    bl_idname = "character_designer.skirt_select_controls"
    bl_label = "Select Skirt Controls"
    bl_description = "Select the native skirt controls in Pose Mode; use G, R, and S to pose them"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _idle(context) and _has_setup(context)

    def execute(self, context):
        try:
            source = _source(context)
            _settings(context).source = source
            _restore_preview(context, source)
            _rig().select_controls(context, source)
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_skirt_add_physics(Operator):
    bl_idname = "character_designer.skirt_add_physics"
    bl_label = "Add Physics + Colliders"
    bl_description = "Create the skirt cloth proxy and closed collision meshes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _idle(context) and _has_setup(context) and not _has_physics(context)

    def execute(self, context):
        try:
            source = _source(context)
            _settings(context).source = source
            _physics().add_physics(context, source)
            _rig().select_controls(context, source)
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, "Physics ready. Check the colliders, then bake the animation range.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_skirt_toggle_helpers(Operator):
    bl_idname = "character_designer.skirt_toggle_helpers"
    bl_label = "Show Skirt Helpers"
    bl_description = "Show or hide the skirt wire cage or closed colliders in this view layer"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(items=(("WIRE", "Wire", "Fitted cage and physics proxy"),
                              ("COLLIDERS", "Colliders", "Closed collision meshes")))

    @classmethod
    def poll(cls, context):
        return _idle(context) and _has_setup(context)

    def execute(self, context):
        try:
            source = _source(context)
            _settings(context).source = source
            record = _rig().read_record(source)
            visible = _helpers_visible(record, self.kind, context)
            for obj in _helper_objects(record, self.kind):
                if obj.name in context.view_layer.objects:
                    obj.hide_set(visible, view_layer=context.view_layer)
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_skirt_select_colliders(Operator):
    bl_idname = "character_designer.skirt_select_colliders"
    bl_label = "Select Colliders"
    bl_description = "Select the generated colliders to inspect or adjust their mesh fit"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _idle(context) and _has_physics(context)

    def execute(self, context):
        try:
            source = _source(context)
            colliders = [obj for obj in _helper_objects(_rig().read_record(source), "COLLIDERS")
                         if obj.name in context.view_layer.objects]
            if not colliders:
                raise ValueError("No generated colliders are available in this view layer.")
            _settings(context).source = source
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in colliders:
                obj.hide_set(False)
                obj.hide_select = False
                obj.select_set(True)
            context.view_layer.objects.active = colliders[0]
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, "Adjust collider vertices in Edit Mode. Clear the cache after changes.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_skirt_clear_cache(Operator):
    bl_idname = "character_designer.skirt_clear_cache"
    bl_label = "Clear Physics Cache"
    bl_description = "Invalidate the skirt physics cache after posing, timing, or collider changes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _idle(context) and _has_physics(context)

    def execute(self, context):
        try:
            _physics().clear_cache(context, _source(context))
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, "Skirt physics cache cleared. Bake again after your changes.")
        return {"FINISHED"}


class _SkirtBakeOperator:
    """Advance every frame without allowing a second bake to replace this one."""

    kind = "SIMULATION"
    _steps = None
    _timer = None
    _wm = None
    _bake_source = None

    @classmethod
    def poll(cls, context):
        return _idle(context) and _settings(context) is not None and _has_physics(context)

    def _finish(self):
        wm = self._wm
        if wm is None:
            return
        if self._timer is not None:
            try:
                wm.event_timer_remove(self._timer)
            except (ReferenceError, RuntimeError):
                pass
            self._timer = None
        wm.progress_end()
        _ACTIVE_BAKES.pop(wm.as_pointer(), None)
        self._wm = None
        self._steps = None

    def _close(self):
        try:
            if self._steps is not None:
                self._steps.close()
        finally:
            self._finish()

    def _complete_message(self, start, end):
        if self.kind == "ANIMATION":
            return f"Baked animation copy created for frames {start}–{end}. Original controls are preserved."
        return f"Skirt physics baked continuously for frames {start}–{end}."

    def execute(self, context):
        # Direct execution also works in background Blender and test scripts.
        try:
            source = _source(context)
            _settings(context).source = source
            _restore_preview(context, source)
            start, end = _bake_range(context)
            callback = (_physics().bake_animation if self.kind == "ANIMATION"
                        else _physics().bake_simulation)
            result = callback(context, source, start, end)
            if self.kind == "ANIMATION":
                _remember_bake(source, result)
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, self._complete_message(start, end))
        return {"FINISHED"}

    def invoke(self, context, event):
        if context.window is None or bpy.app.background:
            return self.execute(context)
        try:
            source = _source(context)
            _settings(context).source = source
            _restore_preview(context, source)
            self._bake_source = source
            self._start, self._end = _bake_range(context)
            self._steps = _physics().bake_steps(
                context, source, self._start, self._end, kind=self.kind,
            )
            self._wm = context.window_manager
            self._wm.progress_begin(0, 1)
            _ACTIVE_BAKES[self._wm.as_pointer()] = self
            # Validation runs before installing a modal handler.
            result = self._advance(context)
            if result is not None:
                return result
            self._timer = self._wm.event_timer_add(0.01, window=context.window)
            self._wm.modal_handler_add(self)
        except (ValueError, RuntimeError) as exc:
            self._close()
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _advance(self, context):
        try:
            completed, total, message = next(self._steps)
        except StopIteration as completed:
            if self.kind == "ANIMATION":
                _remember_bake(self._bake_source, completed.value)
            self._finish()
            _report(self, context, self._complete_message(self._start, self._end))
            return {"FINISHED"}
        except (ValueError, RuntimeError) as exc:
            self._close()
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        self._wm.progress_update(completed / max(1, total))
        _settings(context).last_message = message
        if context.area is not None:
            context.area.tag_redraw()
        return None

    def modal(self, context, event):
        if self._steps is None:
            return {"CANCELLED"}
        if event.type == "ESC":
            self._close()
            _report(self, context, "Skirt bake cancelled. Bake the complete range before playback.")
            return {"CANCELLED"}
        if event.type == "TIMER" and event.timer == self._timer:
            return self._advance(context) or {"RUNNING_MODAL"}
        # Keep transform/timeline input from changing the simulation mid-bake.
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        self._close()


class CHARACTERDESIGNER_OT_skirt_bake_physics(_SkirtBakeOperator, Operator):
    bl_idname = "character_designer.skirt_bake_physics"
    bl_label = "Bake Physics"
    bl_description = "Evaluate and cache every frame in order for stable physics playback"
    bl_options = {"REGISTER", "UNDO"}
    kind = "SIMULATION"


class CHARACTERDESIGNER_OT_skirt_bake_animation(_SkirtBakeOperator, Operator):
    bl_idname = "character_designer.skirt_bake_animation"
    bl_label = "Bake Animation Copy"
    bl_description = "Bake final skirt bone motion into a separate mesh and armature with keyframes"
    bl_options = {"REGISTER", "UNDO"}
    kind = "ANIMATION"


class CHARACTERDESIGNER_OT_skirt_preview_bake(Operator):
    bl_idname = "character_designer.skirt_preview_bake"
    bl_label = "Inspect Baked Animation"
    bl_description = "Preview the latest baked skirt copy; return to controls to restore viewport visibility"
    bl_options = {"REGISTER", "UNDO"}

    restore: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _idle(context) and _last_bake(_source(context)) is not None

    def execute(self, context):
        try:
            source = _source(context)
            _settings(context).source = source
            if self.restore:
                _restore_preview(context, source)
                if context.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                for obj in context.selected_objects:
                    obj.select_set(False)
                source.hide_set(False)
                source.select_set(True)
                context.view_layer.objects.active = source
            else:
                _show_baked(context, source)
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, "Source restored." if self.restore else
                "Showing the baked animation copy. Return to Controls to continue editing.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_remove_skirt_setup(Operator):
    bl_idname = "character_designer.remove_skirt_setup"
    bl_label = "Remove Skirt Setup"
    bl_description = "Remove this tool's generated skirt rig, physics, and weights; restore source bindings"
    bl_options = {"REGISTER", "UNDO"}

    authorize_animated_removal: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _idle(context) and _has_setup(context)

    def invoke(self, context, event):
        try:
            record = _record(context)
            self.authorize_animated_removal = bool(record) and any(
                obj.get(_rig().OWNER_KEY) == record["owner"] and obj.animation_data
                and (obj.animation_data.action or obj.animation_data.nla_tracks)
                for obj in bpy.data.objects
            )
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        return context.window_manager.invoke_confirm(
            self, event, title="Remove Skirt Setup?", confirm_text="Remove Setup", icon="WARNING",
            message="Generated controls and their animation will be removed. Baked copies remain.",
        )

    def execute(self, context):
        try:
            source = _source(context)
            _restore_preview(context, source)
            _rig().remove_skirt(context, source, allow_animation=self.authorize_animated_removal)
            _settings(context).source = source
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        _report(self, context, "Generated skirt setup removed. Source bindings restored.")
        return {"FINISHED"}


class CHARACTERDESIGNER_PT_skirt_setup(Panel):
    bl_label = "Skirt Setup"
    bl_idname = "CHARACTERDESIGNER_PT_skirt_setup"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_CLOTHING

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        if settings is None:
            return
        try:
            source = _source(context)
            record = _rig().read_record(source) if source is not None else None
        except (ValueError, RuntimeError) as exc:
            layout.label(text=str(exc), icon="ERROR")
            return
        if source is not None:
            layout.label(text=f"Skirt: {source.name}", icon="OUTLINER_OB_MESH")
        else:
            layout.label(text="Select an open skirt mesh.", icon="INFO")
            layout.prop(settings, "source")
        if not _idle(context):
            layout.label(text="Baking every frame in order…", icon="TIME")
            layout.label(text=settings.last_message)
            layout.label(text="Esc to cancel.")
            return
        if not record:
            row = layout.row(align=True)
            row.prop(settings, "chain_count")
            row.prop(settings, "segment_count", text="Bones")
            layout.prop(settings, "physics")
            layout.prop(settings, "show_attachment", icon=("TRIA_DOWN" if settings.show_attachment
                                                             else "TRIA_RIGHT"), emboss=False)
            if settings.show_attachment:
                col = layout.column(align=True)
                col.prop(settings, "armature")
                if settings.armature is not None:
                    col.prop_search(settings, "parent_bone", settings.armature.data, "bones")
                else:
                    col.prop(settings, "parent_bone")
                col.label(text="Leave blank for automatic detection.")
            row = layout.row()
            row.scale_y = 1.4
            row.operator("character_designer.create_skirt_setup", icon="OUTLINER_OB_ARMATURE")
            layout.label(text="Fits the waist, hem, and wire cage.", icon="INFO")
            if _last_bake(source):
                restoring = PREVIEW_KEY in source
                operator = layout.operator("character_designer.skirt_preview_bake", icon="PLAY",
                                           text="Return to Source" if restoring else "Inspect Baked Animation")
                operator.restore = restoring
            return

        layout.label(text=f'{record.get("chain_count", "?")} chains × '
                          f'{record.get("segment_count", "?")} bones', icon="BONE_DATA")
        layout.operator("character_designer.skirt_select_controls", icon="POSE_HLT",
                        text="Return to Controls" if PREVIEW_KEY in source else "Select Skirt Controls")
        layout.label(text="Rings: G / R / S to move, rotate, and scale.")
        layout.label(text="Side points: G to shape the wire.")
        row = layout.row(align=True)
        wire = row.operator("character_designer.skirt_toggle_helpers", text="Wire", icon="SHADING_WIRE",
                            depress=_helpers_visible(record, "WIRE", context))
        wire.kind = "WIRE"
        physics = record.get("physics") or {}
        if physics:
            colliders = row.operator("character_designer.skirt_toggle_helpers", text="Colliders",
                                     icon="MESH_ICOSPHERE",
                                     depress=_helpers_visible(record, "COLLIDERS", context))
            colliders.kind = "COLLIDERS"
            layout.operator("character_designer.skirt_select_colliders", icon="RESTRICT_SELECT_OFF")
        else:
            layout.operator("character_designer.skirt_add_physics", icon="PHYSICS")

        if physics:
            rig = bpy.data.objects.get(record.get("rig", ""))
            if rig is not None and "physics_influence" in rig:
                row = layout.row(align=True)
                row.use_property_decorate = True
                row.prop(rig, '["physics_influence"]', text="Physics", slider=True)
            layout.prop(settings, "use_scene_range")
            if settings.use_scene_range:
                layout.label(text=f"Frames {context.scene.frame_start}–{context.scene.frame_end}")
            else:
                row = layout.row(align=True)
                row.prop(settings, "bake_start")
                row.prop(settings, "bake_end")
            row = layout.row(align=True)
            row.operator("character_designer.skirt_bake_physics", icon="REC")
            row.operator("character_designer.skirt_clear_cache", text="Clear", icon="X")
            layout.operator("character_designer.skirt_bake_animation", icon="ACTION")
            if _last_bake(source):
                layout.operator("character_designer.skirt_preview_bake", icon="PLAY")
            layout.label(text="Clear and rebake after pose or collider edits.", icon="INFO")
        layout.separator()
        remove_row = layout.row()
        remove_row.alert = True
        remove_row.operator("character_designer.remove_skirt_setup", icon="TRASH")


def stop_skirt_runtime():
    """Close incomplete bake jobs before add-on reload or unregister."""
    for operator in list(_ACTIVE_BAKES.values()):
        try:
            operator._close()
        except (ReferenceError, RuntimeError, ValueError):
            pass
    _ACTIVE_BAKES.clear()


SKIRT_CLASSES = (
    CharacterDesignerSkirtState,
    CHARACTERDESIGNER_OT_create_skirt_setup,
    CHARACTERDESIGNER_OT_skirt_select_controls,
    CHARACTERDESIGNER_OT_skirt_add_physics,
    CHARACTERDESIGNER_OT_skirt_toggle_helpers,
    CHARACTERDESIGNER_OT_skirt_select_colliders,
    CHARACTERDESIGNER_OT_skirt_clear_cache,
    CHARACTERDESIGNER_OT_skirt_bake_physics,
    CHARACTERDESIGNER_OT_skirt_bake_animation,
    CHARACTERDESIGNER_OT_skirt_preview_bake,
    CHARACTERDESIGNER_OT_remove_skirt_setup,
    CHARACTERDESIGNER_PT_skirt_setup,
)
