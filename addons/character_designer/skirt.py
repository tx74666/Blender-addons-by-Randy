"""Rig / Skirt workflow for fitted controls, attachment, and physics baking."""

import importlib
import json
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from .ui_constants import SIDEBAR_CATEGORY, rig_page_active


_ACTIVE_BAKES = {}
LAST_BAKE_KEY = "character_designer_skirt_last_bake"
PREVIEW_KEY = "character_designer_skirt_bake_preview"


def _rig():
    return importlib.import_module(__package__ + ".skirt_rig")


def _physics():
    return importlib.import_module(__package__ + ".skirt_physics")


def _add_requested_physics(context, source, actual_surface, *, capability=None):
    """The panel opts into the surface backend; legacy scripted calls stay valid."""
    physics = _physics()
    if not actual_surface:
        return physics.add_physics(context, source)
    setup = _setup().settings(context)
    body = setup.body if setup is not None else None
    if body is None or body.type != "MESH":
        raise ValueError("Choose the actual Body mesh in Character Setup before adding Direct Dress physics.")
    return physics.add_physics(context, source,
                               backend=physics.DIRECT_SURFACE_BACKEND, body=body, capability=capability)


def _setup():
    return importlib.import_module(__package__ + ".character_setup")


def _settings(context):
    return getattr(context.window_manager, "character_designer_skirt", None)


def _source(context):
    """Prefer the selected source/owned helper, then the last explicit source."""
    source = _rig().find_source(context)
    if source is not None:
        return source
    settings = _settings(context)
    return (settings.source if settings is not None else None) or _setup().role_source(context, "SKIRT")


def _record(context):
    source = _source(context)
    return _rig().read_record(source) if source is not None else None


def _desired_attachment(context, source):
    settings = _settings(context)
    armature = _setup().preferred_rig(context, settings.armature)
    if armature is None:
        if source is None:
            return None, ''
        armature, bone = _rig()._find_character(context, source, None, settings.parent_bone)
        if armature is None:
            return None, ''
    bone = _setup().resolve_bone(context, 'HIPS', armature=armature,
                                override=settings.parent_bone)
    return armature, bone


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


def _colliders(record):
    """Only independent fitting meshes, never the shared Body collision input."""
    physics = record.get("physics") or {}
    surface = physics.get("surface") or {}
    protected = set(surface.get("roles", {}).get("BODY_ATTACHMENT", ()))
    if surface.get("body"):
        protected.add(surface["body"])
    return [obj for name in physics.get("colliders", ())
            if name not in protected and (obj := bpy.data.objects.get(name)) is not None
            and obj.get("character_designer_skirt_surface_role") != "BODY_ATTACHMENT"]


def _helper_objects(record, kind):
    physics = record.get("physics") or {}
    if kind == "COLLIDERS":
        return _colliders(record)
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
        rig = source.get(_rig().RIG_KEY) if snapshot.get('dress_bones') else None
        if snapshot.get('dress_bones'):
            if rig is None:
                raise ValueError('The shared dress rig was removed during its preview.')
            for name in snapshot['dress_bones']:
                bone = rig.data.bones.get(name)
                if bone is None or bone.get(_rig().OWNER_KEY) != snapshot['dress_owner']:
                    raise ValueError('The shared dress bones changed during their preview.')
        view_layer = context.scene.view_layers.get(snapshot["view_layer"])
        if view_layer is not None:
            for name, hidden in snapshot["objects"].items():
                obj = bpy.data.objects.get(name)
                if obj is not None and obj.name in view_layer.objects:
                    obj.hide_set(hidden, view_layer=view_layer)
        collection = bpy.data.collections.get(snapshot["collection"])
        if collection is not None:
            collection.hide_viewport = snapshot["collection_hidden"]
        if snapshot.get('dress_bones'):
            for name, flags in snapshot['dress_bones'].items():
                rig.data.bones[name].hide = flags[0]
                pb = rig.pose.bones[name]
                if hasattr(pb, 'hide'):
                    pb.hide = flags[1]
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
    if record and _rig().is_shared(record):
        rig = source[_rig().RIG_KEY]
        snapshot['dress_owner'] = record['owner']
        snapshot['dress_bones'] = {name: [rig.data.bones[name].hide,
                                          getattr(rig.pose.bones[name], 'hide', rig.data.bones[name].hide)]
                                   for name in record['shared']['names']}
    source[PREVIEW_KEY] = json.dumps(snapshot, ensure_ascii=False)
    try:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in visible_objects:
            obj.hide_set(True, view_layer=context.view_layer)
        if snapshot.get('dress_bones'):
            from . import bone_display
            for name in snapshot['dress_bones']:
                bone_display._set_hidden(rig, rig.data.bones[name], True)
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
    physics: BoolProperty(name="Physics + Colliders", default=True, options={"SKIP_SAVE", "HIDDEN"},
                          description="Compatibility flag for scripted setup; use Generation in the panel")
    capability: EnumProperty(name="Generation", default="BOTH", options={"SKIP_SAVE"}, items=(
        ("BOTH", "Physics + Manual", "Automatic Cloth with optional manual shaping tools"),
        ("PHYSICS", "Physics", "Automatic Cloth; daily controls remain simple"),
        ("MANUAL", "Manual", "Wire controls without a Cloth simulation")))
    show_manual: BoolProperty(name="Manual Refinement", default=False, options={"SKIP_SAVE"})
    show_attachment: BoolProperty(name="Attachment Override", default=False,
                                  description="Optional local target; leave fields blank to use Character Setup",
                                  options={"SKIP_SAVE"})
    armature: PointerProperty(type=bpy.types.Object, name="Character Rig", poll=_armature_only,
                              options={"SKIP_SAVE"},
                              description="Optional override; otherwise use the saved Main Rig or detect the character rig")
    parent_bone: StringProperty(name="Attachment Bone", options={"SKIP_SAVE"},
                                description="Single bone followed by the whole skirt, usually central Hips; blank uses Character Setup")
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

    actual_surface: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _idle(context) and _settings(context) is not None and _source(context) is not None

    def execute(self, context):
        source = _source(context)
        settings = _settings(context)
        had_setup = False
        built = False
        try:
            record = _rig().read_record(source)
            had_setup = record is not None
            update_capability = None
            if had_setup and self.actual_surface:
                # Read and validate saved intent before restoring a preview or
                # changing the native graph. Only an explicit legacy Manual
                # upgrade enables Automatic; existing Both/Physics modes stay.
                from . import skirt_motion_profiles
                backend = _physics().backend(record)
                saved_profile = skirt_motion_profiles.read(source, record)
                if (backend == _physics().LEGACY_BACKEND and saved_profile is not None
                        and saved_profile["capability"] == "MANUAL"):
                    update_capability = "BOTH"
            _restore_preview(context, source)
            settings.source = source
            capability = settings.capability
            # Preserve existing scripts that explicitly opted out of physics.
            if not settings.is_property_set("capability") and settings.is_property_set("physics"):
                capability = "BOTH" if settings.physics else "MANUAL"
            if had_setup and self.actual_surface:
                # Explicitly update this installed graph, independent of the
                # creation panel's dimensions or generation preset.
                _rig()._require_controls_for_setup(source)
                _add_requested_physics(context, source, self.actual_surface, capability=update_capability)
            else:
                armature, bone = (None, '') if had_setup else _desired_attachment(context, source)
                record = _rig().build_skirt(
                    context, source, chain_count=settings.chain_count,
                    segment_count=settings.segment_count,
                    armature=armature,
                    parent_bone=bone,
                )
                built = True
                if capability != "MANUAL":
                    _add_requested_physics(context, source, self.actual_surface, capability=capability)
            from . import skirt_motion_tuning
            if not (self.actual_surface and _physics().backend(_rig().read_record(source)) in _physics().SURFACE_BACKENDS):
                skirt_motion_tuning.initialize(source, capability=capability if not had_setup else None, context=context)
            _rig().select_controls(context, source)
            record = _rig().read_record(source) or record
            _setup().remember_asset(context, source, "SKIRT")
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
        message = f"Dress ready: {count} chains × {segments} bones."
        message += " Reset to the start frame and play or bake." if record.get("physics") else " Pose the waist and wire controls."
        _report(self, context, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_skirt_update_attachment(Operator):
    bl_idname = 'character_designer.skirt_update_attachment'
    bl_label = 'Update Attachment'
    bl_description = 'Follow the chosen main bone while keeping the current skirt position and control animation'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _idle(context) and _has_setup(context)

    def execute(self, context):
        try:
            source = _source(context)
            armature, bone = _desired_attachment(context, source)
            if armature is None:
                raise ValueError('Set Main Rig and its Hips bone in Character Setup first.')
            _restore_preview(context, source)
            _rig().update_attachment(context, source, armature, bone)
            _report(self, context, f'Following {armature.name} / {bone}.')
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_skirt_unify_rig(Operator):
    bl_idname = 'character_designer.skirt_unify_rig'
    bl_label = 'Use Main Rig'
    bl_description = 'Move this dress skeleton into the Main Rig while preserving its bindings, controls and pose'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        try:
            record = _record(context)
            return _idle(context) and bool(record) and not _rig().is_shared(record)
        except (ValueError, RuntimeError, ReferenceError):
            return False

    def execute(self, context):
        try:
            source = _source(context)
            armature, bone = _desired_attachment(context, source)
            if armature is None:
                raise ValueError('Set Main Rig and its Hips bone in Character Setup first.')
            _restore_preview(context, source)
            record = _rig().unify_skirt(context, source, armature, bone)
            _settings(context).source = source
            _report(self, context, f'Dress is part of {armature.name}. Body, Hair and Dress now share Pose Mode.')
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
            _report(self, context, str(exc), error=True)
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_skirt_restore_attachment(Operator):
    bl_idname = 'character_designer.skirt_restore_attachment'
    bl_label = 'Restore Attachment'
    bl_description = 'Restore the original attachment saved before the first update, including its animation space'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        try:
            return _idle(context) and _rig().has_attachment_backup(_source(context))
        except (ValueError, RuntimeError):
            return False

    def execute(self, context):
        try:
            source = _source(context)
            _restore_preview(context, source)
            _rig().restore_attachment(context, source)
            _report(self, context, 'Original skirt attachment restored.')
        except (ValueError, RuntimeError) as exc:
            _report(self, context, str(exc), error=True)
            return {'CANCELLED'}
        return {'FINISHED'}


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

    actual_surface: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _idle(context) and _has_setup(context) and not _has_physics(context)

    def execute(self, context):
        new_direct = False
        selection_state = None
        try:
            source = _source(context)
            settings = _settings(context)
            if self.actual_surface:
                physics, rig_service = _physics(), _rig()
                original_record = rig_service.read_record(source)
                if physics.backend(original_record) != physics.DIRECT_SURFACE_BACKEND:
                    provider = physics._surface_module(backend_name=physics.DIRECT_SURFACE_BACKEND)
                    rig = source[rig_service.RIG_KEY]
                    control = rig.data.bones[original_record["controls"]["waist"]]
                    pose_control = rig.pose.bones[control.name]
                    selection_state = {
                        "context": rig_service._context_state(context), "source": settings.source,
                        "selected": [(bone, bone.select) for bone in rig.pose.bones],
                        "active": rig.data.bones.active, "hidden": rig.hide_get(),
                        "control_hidden": control.hide,
                        "pose_hidden": pose_control.hide if hasattr(pose_control, "hide") else None,
                        "collections": [(collection, collection.is_visible) for collection in control.collections],
                        "modifiers": [(modifier, modifier.is_active) for modifier in source.modifiers],
                    }
            settings.source = source
            _add_requested_physics(context, source, self.actual_surface, capability="BOTH")
            new_direct = selection_state is not None
            from . import skirt_motion_tuning
            if not self.actual_surface:
                skirt_motion_tuning.initialize(source, capability="BOTH", context=context)
            try:
                _rig().select_controls(context, source)
            except (ValueError, RuntimeError) as exc:
                if not new_direct:
                    raise
                failures = []
                try:
                    record = rig_service.read_record(source)
                    plan = provider.preflight_remove(context, source, rig, record)
                    provider.commit_remove(source, plan)
                except Exception as rollback_error:
                    failures.append("Direct installation: " + str(rollback_error))
                try:
                    rig_service._restore_context(context, selection_state["context"])
                except Exception as rollback_error:
                    failures.append("object context: " + str(rollback_error))
                try:
                    for bone, selected in selection_state["selected"]:
                        bone.select = selected
                    rig.data.bones.active = selection_state["active"]
                    for collection, visible in selection_state["collections"]:
                        collection.is_visible = visible
                    control.hide = selection_state["control_hidden"]
                    if selection_state["pose_hidden"] is not None:
                        pose_control.hide = selection_state["pose_hidden"]
                    rig.hide_set(selection_state["hidden"])
                except Exception as rollback_error:
                    failures.append("control selection/display: " + str(rollback_error))
                try:
                    settings.source = selection_state["source"]
                    for modifier, active in selection_state["modifiers"]:
                        modifier.is_active = active
                except Exception as rollback_error:
                    failures.append("source selection/modifier UI: " + str(rollback_error))
                _report(self, context, str(exc), error=True)
                for failure in failures:
                    _report(self, context, "Direct rollback needs recovery: " + failure, error=True)
                return {"CANCELLED"}
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
        checkpoint = None
        try:
            source = _source(context)
            record, rig, proxy, cloth = _physics().validate_physics(source)
            surface = None
            body = None
            updated = record
            if _physics().backend(record) in _physics().SURFACE_BACKENDS:
                surface = _physics()._surface_module(record)
                updated, candidates, body = surface.prepare_collider_fitting(source, rig, record)
            else:
                cache = cloth.point_cache
                if (cache.is_baked or cache.is_baking or cache.use_external
                        or record["physics"].get("baked_range")):
                    raise ValueError("Reset Dress before fitting its colliders. Finish any bake and disable external cache first.")
                candidates = _colliders(record)
            colliders = [obj for obj in candidates if obj.name in context.view_layer.objects]
            if not colliders:
                raise ValueError("No generated colliders are available in this view layer.")
            selected = tuple(context.selected_objects)
            changed = list(colliders) + ([body] if body is not None else [])
            checkpoint = {"source": source, "raw": source[_rig().RECORD_KEY],
                          "settings_source": _settings(context).source,
                          "active": context.view_layer.objects.active, "selected": selected,
                          "mode": context.view_layer.objects.active.mode if context.view_layer.objects.active else "OBJECT",
                          "flags": [(obj, obj.hide_get(view_layer=context.view_layer)
                                     if obj.name in context.view_layer.objects else None, obj.hide_select)
                                    for obj in changed]}
            _settings(context).source = source
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for obj in context.selected_objects:
                obj.select_set(False)
            if body is not None:
                body.hide_select = True
            for obj in colliders:
                obj.hide_set(False, view_layer=context.view_layer)
                obj.hide_select = False
                obj.select_set(True)
            context.view_layer.objects.active = colliders[0]
            if surface is not None:
                _rig().write_record(source, updated)
                surface.validate(source, rig, updated)
        except (ValueError, RuntimeError) as exc:
            if checkpoint is not None:
                checkpoint["source"][_rig().RECORD_KEY] = checkpoint["raw"]
                _settings(context).source = checkpoint["settings_source"]
                if context.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                for obj in context.selected_objects:
                    obj.select_set(False)
                for obj, hidden, restricted in checkpoint["flags"]:
                    if hidden is not None:
                        obj.hide_set(hidden, view_layer=context.view_layer)
                    obj.hide_select = restricted
                for obj in checkpoint["selected"]:
                    obj.select_set(True)
                context.view_layer.objects.active = checkpoint["active"]
                if checkpoint["active"] is not None and checkpoint["mode"] != "OBJECT":
                    bpy.ops.object.mode_set(mode=checkpoint["mode"])
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
    _timer_interval = 0.01
    _next_step_time = None
    _wm = None
    _bake_source = None

    @classmethod
    def poll(cls, context):
        return _idle(context) and _settings(context) is not None and _has_physics(context)

    def _finish(self):
        wm = self._wm
        timer = self._timer
        self._timer = None
        self._next_step_time = None
        if wm is not None:
            if timer is not None:
                try:
                    wm.event_timer_remove(timer)
                except (ReferenceError, RuntimeError):
                    pass
            try:
                wm.progress_end()
            except (ReferenceError, RuntimeError):
                pass
        # A removed window may no longer expose a usable RNA pointer.
        for key, operator in tuple(_ACTIVE_BAKES.items()):
            if operator is self:
                _ACTIVE_BAKES.pop(key, None)
        self._wm = None
        self._steps = None
        self._bake_source = None

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
            self._timer = self._wm.event_timer_add(self._timer_interval, window=context.window)
            self._next_step_time = time.monotonic() + self._timer_interval
            self._wm.modal_handler_add(self)
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self._close()
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _advance(self, context):
        try:
            completed, total, message = next(self._steps)
            self._wm.progress_update(completed / max(1, total))
            _settings(context).last_message = message
            if context.area is not None:
                context.area.tag_redraw()
        except StopIteration as completed:
            try:
                if self.kind == "ANIMATION":
                    _remember_bake(self._bake_source, completed.value)
            finally:
                self._finish()
            _report(self, context, self._complete_message(self._start, self._end))
            return {"FINISHED"}
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self._close()
            _report(self, context, str(exc), error=True)
            return {"CANCELLED"}
        return None

    def modal(self, context, event):
        if self._steps is None:
            return {"CANCELLED"}
        if event.type == "ESC":
            self._close()
            _report(self, context, "Skirt bake cancelled. Bake the complete range before playback.")
            return {"CANCELLED"}
        if event.type == "TIMER" and self._timer is not None:
            # Native Event has no timer identity. Other window timers can wake
            # this operator, but each wake advances at most one sequential frame
            # and only once per interval; queued events never catch up in a burst.
            now = time.monotonic()
            if self._next_step_time is not None and now >= self._next_step_time:
                self._next_step_time = now + self._timer_interval
                result = self._advance(context)
                if self._steps is not None:
                    self._next_step_time = time.monotonic() + self._timer_interval
                return result or {"RUNNING_MODAL"}
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
            if record and _rig().is_shared(record):
                rig = _source(context)[_rig().RIG_KEY]
                self.authorize_animated_removal |= bool(rig.animation_data
                    and (rig.animation_data.action or rig.animation_data.nla_tracks))
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
        return rig_page_active(context, 'SKIRT')

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
        preferred = _setup().preferred_rig(context, settings.armature)
        desired, desired_bone, attachment_error = None, '', ''
        try:
            desired, desired_bone = _desired_attachment(context, source)
        except (ValueError, RuntimeError) as exc:
            attachment_error = str(exc)
        if record:
            try:
                actual = _rig().attachment_status(source)
            except (ValueError, RuntimeError) as exc:
                layout.label(text=str(exc), icon='ERROR')
                return
            if _rig().is_shared(record):
                layout.label(text=f"Rig: {actual['character'].name} / Dress", icon='OUTLINER_OB_ARMATURE')
            elif actual['attached']:
                layout.label(text=f"Following: {actual['character'].name} / {actual['parent_bone']}",
                             icon='CONSTRAINT_BONE')
            else:
                layout.label(text='Not attached to a main bone.', icon='INFO')
            different = desired is not actual['character'] or desired_bone != actual['parent_bone']
            if attachment_error:
                layout.label(text=attachment_error, icon='ERROR')
            elif different and desired is not None:
                layout.label(text=f'New target: {desired.name} / {desired_bone}', icon='INFO')
            elif actual['attached']:
                layout.label(text='Connected', icon='CHECKMARK')
        else:
            if attachment_error:
                layout.label(text=attachment_error, icon='ERROR')
            elif desired is not None:
                layout.label(text=f'Attach to: {desired.name} / {desired_bone}', icon='CONSTRAINT_BONE')
            else:
                layout.label(text='Set Main Rig to follow your character.', icon='INFO')
        layout.prop(settings, 'show_attachment',
                    icon='TRIA_DOWN' if settings.show_attachment else 'TRIA_RIGHT', emboss=False)
        if settings.show_attachment:
            col = layout.column(align=True)
            col.prop(settings, 'armature')
            if preferred is not None:
                col.prop_search(settings, 'parent_bone', preferred.data, 'bones')
            else:
                col.prop(settings, 'parent_bone')
            col.label(text='Blank fields use Character Setup.')
        if record:
            if not _rig().is_shared(record):
                row = layout.row()
                row.enabled = not attachment_error and desired is not None
                row.operator('character_designer.skirt_unify_rig', text='Use Main Rig')
            row = layout.row()
            row.enabled = not attachment_error and desired is not None and not (actual['physics'] and different)
            row.operator('character_designer.skirt_update_attachment', icon='CONSTRAINT_BONE')
            if actual['physics'] and different:
                layout.label(text='Existing physics prevents changing attachment.', icon='INFO')
            if actual['has_backup']:
                row = layout.row()
                row.operator('character_designer.skirt_restore_attachment', icon='LOOP_BACK')
        if not record:
            row = layout.row(align=True)
            row.prop(settings, "chain_count")
            row.prop(settings, "segment_count", text="Bones")
            layout.prop(settings, "capability")
            row = layout.row()
            row.scale_y = 1.4
            row.enabled = not attachment_error
            row.operator("character_designer.create_skirt_setup", text="Setup Dress",
                         icon="OUTLINER_OB_ARMATURE").actual_surface = True
            layout.label(text="Fits the waist, hem, and wire cage.", icon="INFO")
            if _last_bake(source):
                restoring = PREVIEW_KEY in source
                operator = layout.operator("character_designer.skirt_preview_bake", icon="PLAY",
                                           text="Return to Source" if restoring else "Inspect Baked Animation")
                operator.restore = restoring
            return

        layout.label(text=f'{record.get("chain_count", "?")} chains × '
                          f'{record.get("segment_count", "?")} bones', icon="BONE_DATA")
        from .skirt_motion_ui import draw_motion
        draw_motion(layout, context, source, record)
        from . import body_original_mode, skirt_original_mode
        dress_rig = source.get(_rig().RIG_KEY)
        original_edit = body_original_mode.active(dress_rig)
        if dress_rig is not None:
            original_edit |= body_original_mode.active(
                dress_rig.get(_rig().ORIGINAL_DISPLAY_OWNER_KEY))
        if skirt_original_mode.CORRECTIONS in source:
            row = layout.row(align=True)
            row.enabled = not original_edit
            row.operator('character_designer.clear_dress_corrections',
                         text='Clear Dress Pose').scope = 'ALL'
            row.operator('character_designer.clear_dress_corrections',
                         text='Selected').scope = 'SELECTED'
        physics = record.get("physics") or {}
        actual_surface = physics.get("backend") in _physics().SURFACE_BACKENDS
        if not physics:
            layout.operator("character_designer.skirt_add_physics", icon="PHYSICS").actual_surface = True
        elif not actual_surface and physics.get("backend", "LEGACY_CAGE") == "LEGACY_CAGE":
            layout.operator("character_designer.create_skirt_setup", text="Update Dress Physics",
                            icon="PHYSICS").actual_surface = True
        if physics:
            layout.prop(settings, "use_scene_range")
            if settings.use_scene_range:
                layout.label(text=f"Frames {context.scene.frame_start}–{context.scene.frame_end}")
            else:
                row = layout.row(align=True)
                row.prop(settings, "bake_start")
                row.prop(settings, "bake_end")
        layout.operator("character_designer.skirt_details", text="Manual Refinement & Export",
                        icon="TRIA_DOWN" if settings.show_manual else "TRIA_RIGHT", emboss=False)
        if settings.show_manual:
            box = layout.box()
            box.operator("character_designer.skirt_select_controls", icon="POSE_HLT",
                         text="Return to Controls" if PREVIEW_KEY in source else "Select Skirt Controls")
            box.label(text="Rings: G / R / S. Side points: G.")
            row = box.row(align=True)
            row.operator("character_designer.skirt_toggle_helpers", text="Wire", icon="SHADING_WIRE",
                         depress=_helpers_visible(record, "WIRE", context)).kind = "WIRE"
            if physics:
                row.operator("character_designer.skirt_toggle_helpers", text="Colliders", icon="MESH_ICOSPHERE",
                             depress=_helpers_visible(record, "COLLIDERS", context)).kind = "COLLIDERS"
                box.operator("character_designer.skirt_select_colliders", icon="RESTRICT_SELECT_OFF")
                if not actual_surface:
                    holder, _rig_object, _path = _rig().physics_control(source)
                    if holder is not None and "physics_influence" in holder:
                        box.prop(holder, '["physics_influence"]', text="Physics Blend", slider=True)
                    box.operator("character_designer.skirt_bake_animation", icon="ACTION")
                    if _last_bake(source):
                        box.operator("character_designer.skirt_preview_bake", icon="PLAY")
                if physics.get("backend") == "DIRECT_MAIN_CLOTH_V1":
                    box.label(text="Manual shapes the input before Cloth.")
                else:
                    box.label(text="Baked physics retains manual shaping and Dress pose corrections.")
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


class CHARACTERDESIGNER_OT_skirt_details(Operator):
    bl_idname = "character_designer.skirt_details"
    bl_label = "Show Dress Details"
    bl_description = "Expand or collapse manual refinement and export tools"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            return {"CANCELLED"}
        settings.show_manual = not settings.show_manual
        if context.area:
            context.area.tag_redraw()
        return {"FINISHED"}


from .skirt_motion_ui import DRESS_MOTION_CLASSES

SKIRT_CLASSES = (
    CharacterDesignerSkirtState,
    CHARACTERDESIGNER_OT_create_skirt_setup,
    CHARACTERDESIGNER_OT_skirt_update_attachment,
    CHARACTERDESIGNER_OT_skirt_unify_rig,
    CHARACTERDESIGNER_OT_skirt_restore_attachment,
    CHARACTERDESIGNER_OT_skirt_select_controls,
    CHARACTERDESIGNER_OT_skirt_add_physics,
    CHARACTERDESIGNER_OT_skirt_toggle_helpers,
    CHARACTERDESIGNER_OT_skirt_select_colliders,
    CHARACTERDESIGNER_OT_skirt_clear_cache,
    CHARACTERDESIGNER_OT_skirt_bake_physics,
    CHARACTERDESIGNER_OT_skirt_bake_animation,
    CHARACTERDESIGNER_OT_skirt_preview_bake,
    CHARACTERDESIGNER_OT_remove_skirt_setup,
    CHARACTERDESIGNER_OT_skirt_details,
    *DRESS_MOTION_CLASSES,
    CHARACTERDESIGNER_PT_skirt_setup,
)
