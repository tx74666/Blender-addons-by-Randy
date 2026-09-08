"""Local animation generation and explicit body Action application."""

from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy.app.handlers import persistent

from .animation_runtime import DEFAULT_RUNTIME_ROOT, LocalMotionJob, read_runtime
from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_ANIMATION, active_ui_page


_job = None
_job_window_manager = None


def _armature_poll(_self, obj):
    return obj.type == "ARMATURE"


def _settings(context):
    return context.window_manager.character_designer_animation


def _target(context):
    settings = _settings(context)
    if settings.target:
        return settings.target
    obj = context.object
    if obj and obj.type == "ARMATURE" and not obj.get("character_designer_motion_preview"):
        return obj
    return bpy.data.objects.get("CoshaRig")


def _redraw():
    for wm in bpy.data.window_managers:
        for window in wm.windows:
            if window.screen is None:
                continue
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()


def _poll_job():
    global _job, _job_window_manager
    if _job is None:
        return None
    try:
        settings = _job_window_manager.character_designer_animation
        finished = _job.poll()
        settings.status = _job.status
        if finished:
            settings.result_path = _job.result_path
            settings.has_error = bool(_job.error)
            _job = None
            _job_window_manager = None
        _redraw()
        return None if finished else 0.5
    except Exception as exc:
        try:
            _job_window_manager.character_designer_animation.status = str(exc)
            _job_window_manager.character_designer_animation.has_error = True
        finally:
            stop_animation_runtime()
        return None


def stop_animation_runtime():
    global _job, _job_window_manager
    if bpy.app.timers.is_registered(_poll_job):
        bpy.app.timers.unregister(_poll_job)
    if _job:
        _job.cancel()
    _job = None
    _job_window_manager = None


@persistent
def _animation_load_pre(_unused):
    stop_animation_runtime()


def register_animation_runtime():
    if _animation_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_animation_load_pre)


def unregister_animation_runtime():
    stop_animation_runtime()
    if _animation_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_animation_load_pre)


def _can_restore(context):
    from .animation_retarget import VERSION_KEY, TARGET_KEY
    target = _target(context)
    action = target.animation_data.action if target and target.animation_data else None
    return bool(action and action.get(VERSION_KEY) == 1 and action.get(TARGET_KEY) is target)


class CharacterDesignerAnimationState(PropertyGroup):
    target: PointerProperty(name="Character Rig", type=bpy.types.Object,
                            poll=_armature_poll, options={"SKIP_SAVE"})
    prompt: StringProperty(name="Motion", default="A person walks forward at a relaxed pace.",
                           description="Describe a short body action in English", options={"SKIP_SAVE"})
    duration: FloatProperty(name="Seconds", default=2.0, min=1.0, max=10.0, options={"SKIP_SAVE"})
    seed: IntProperty(name="Seed", default=42, min=0, options={"SKIP_SAVE"})
    start_frame: IntProperty(name="Start Frame", default=1, min=1, options={"SKIP_SAVE"})
    runtime_root: StringProperty(name="Kimodo Folder", default=DEFAULT_RUNTIME_ROOT,
                                 subtype="DIR_PATH", options={"SKIP_SAVE"})
    show_setup: BoolProperty(name="Local Setup", options={"SKIP_SAVE"})
    status: StringProperty(default="", options={"SKIP_SAVE"})
    has_error: BoolProperty(options={"SKIP_SAVE"})
    result_path: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    log_path: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    source: PointerProperty(type=bpy.types.Object, poll=_armature_poll, options={"SKIP_SAVE"})


class CHARACTERDESIGNER_OT_animation_generate(Operator):
    bl_idname = "character_designer.animation_generate"
    bl_label = "Generate Local Motion"
    bl_description = "Generate a short motion on this computer; does not change the character"

    @classmethod
    def poll(cls, context):
        return _job is None

    def execute(self, context):
        global _job, _job_window_manager
        settings = _settings(context)
        if not settings.prompt.strip():
            self.report({"ERROR"}, "Enter a motion description.")
            return {"CANCELLED"}
        try:
            job = LocalMotionJob(bpy.path.abspath(settings.runtime_root), prompt=settings.prompt,
                                 duration=settings.duration, seed=settings.seed)
        except Exception as exc:
            settings.status, settings.has_error = str(exc), True
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _job, _job_window_manager = job, context.window_manager
        settings.log_path = str(job.log_path)
        settings.status, settings.has_error = job.status, False
        # A previous imported preview remains usable while a new job runs.
        settings.result_path = ""
        bpy.app.timers.register(_poll_job, first_interval=0.5)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_animation_cancel(Operator):
    bl_idname = "character_designer.animation_cancel"
    bl_label = "Cancel Generation"

    def execute(self, context):
        global _job, _job_window_manager
        if _job:
            _job.cancel()
        _job, _job_window_manager = None, None
        if bpy.app.timers.is_registered(_poll_job):
            bpy.app.timers.unregister(_poll_job)
        _settings(context).status = "Cancelled"
        return {"FINISHED"}


def import_motion_preview(context, filepath, start_frame):
    """Import into a separate armature without changing frame rate or playback range."""
    from io_anim_bvh import import_bvh
    from bpy_extras.io_utils import axis_conversion

    before = set(bpy.data.objects)
    before_armatures = set(bpy.data.armatures)
    before_actions = set(bpy.data.actions)
    selected = tuple(context.selected_objects)
    active = context.view_layer.objects.active
    frame = context.scene.frame_current
    old_mode = active.mode if active else "OBJECT"
    try:
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        import_bvh.load(context, filepath=str(filepath), target="ARMATURE", global_scale=1.0,
                        frame_start=start_frame, use_fps_scale=True, update_scene_fps=False,
                        update_scene_duration=False, rotate_mode="QUATERNION",
                        global_matrix=axis_conversion(from_forward="-Z", from_up="Y").to_4x4())
        created = [obj for obj in bpy.data.objects if obj not in before and obj.type == "ARMATURE"]
        if len(created) != 1:
            raise RuntimeError("BVH did not produce one motion armature.")
        source = created[0]
        source.name = "Kimodo Preview"
        source["character_designer_motion_preview"] = True
        source.show_in_front = True
        return source
    except Exception:
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in set(bpy.data.objects) - before:
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in set(bpy.data.armatures) - before_armatures:
            if data.users == 0:
                bpy.data.armatures.remove(data)
        for action in set(bpy.data.actions) - before_actions:
            if action.users == 0:
                bpy.data.actions.remove(action)
        raise
    finally:
        context.scene.frame_set(frame)
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            if obj.name in context.view_layer.objects:
                obj.select_set(True)
        context.view_layer.objects.active = active
        if active and old_mode != "OBJECT":
            bpy.ops.object.mode_set(mode=old_mode)


class CHARACTERDESIGNER_OT_animation_import(Operator):
    bl_idname = "character_designer.animation_import"
    bl_label = "Import Preview"
    bl_description = "Import the generated skeleton as a separate preview"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        filepath = bpy.path.abspath(settings.result_path)
        if not Path(filepath).is_file():
            self.report({"ERROR"}, "Generate a motion first.")
            return {"CANCELLED"}
        try:
            source = import_motion_preview(context, filepath, settings.start_frame)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        settings.source = source
        settings.status = "Preview imported. Apply to the character when ready."
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_animation_apply(Operator):
    bl_idname = "character_designer.animation_apply"
    bl_label = "Apply as New Action"
    bl_description = "Transfer the preview to body bones in a new Action"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .animation_retarget import retarget_action
        settings = _settings(context)
        target = _target(context)
        if not settings.source or not target or target == settings.source:
            self.report({"ERROR"}, "Import a preview and choose the character rig.")
            return {"CANCELLED"}
        try:
            retarget_action(context, settings.source, target,
                            start_frame=settings.start_frame,
                            action_name="Kimodo Body", assign=True)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        settings.target = target
        settings.status = "New body Action applied. Previous Action can be restored."
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_animation_restore(Operator):
    bl_idname = "character_designer.animation_restore"
    bl_label = "Restore Previous Action"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _can_restore(context)

    def execute(self, context):
        from .animation_retarget import restore_previous_action
        try:
            restore_previous_action(context, _target(context))
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _settings(context).status = "Previous Action restored; generated Action is retained."
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_animation_open_log(Operator):
    bl_idname = "character_designer.animation_open_log"
    bl_label = "Open Job Folder"

    def execute(self, context):
        path = Path(_settings(context).log_path)
        if path.is_file():
            bpy.ops.wm.path_open(filepath=str(path.parent))
            return {"FINISHED"}
        return {"CANCELLED"}


class CHARACTERDESIGNER_PT_animation(Panel):
    bl_label = "Animation"
    bl_idname = "CHARACTERDESIGNER_PT_animation"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_ANIMATION

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        layout.label(text="Kimodo · Free local generation", icon="ARMATURE_DATA")
        layout.prop(settings, "target")
        if not settings.target and _target(context):
            layout.label(text=f"Using: {_target(context).name}")
        controls = layout.column()
        controls.enabled = _job is None
        controls.prop(settings, "prompt")
        row = controls.row(align=True)
        row.prop(settings, "duration")
        row.prop(settings, "seed")
        controls.prop(settings, "start_frame")
        controls.operator("character_designer.animation_generate", icon="PLAY")
        if _job:
            layout.operator("character_designer.animation_cancel", icon="CANCEL")
        if settings.status:
            box = layout.box()
            box.alert = settings.has_error
            import textwrap
            for line in textwrap.wrap(settings.status, width=40):
                box.label(text=line)
        row = layout.row()
        row.enabled = bool(settings.result_path) and _job is None
        row.operator("character_designer.animation_import", icon="IMPORT")
        if settings.source:
            layout.label(text=f"Preview: {settings.source.name}")
            layout.operator("character_designer.animation_apply", icon="ACTION")
        if _can_restore(context):
            layout.operator("character_designer.animation_restore", icon="LOOP_BACK")
        if settings.log_path:
            layout.operator("character_designer.animation_open_log", icon="FILE_FOLDER")
        layout.prop(settings, "show_setup", icon="TRIA_DOWN" if settings.show_setup else "TRIA_RIGHT",
                    emboss=False)
        if settings.show_setup:
            layout.prop(settings, "runtime_root")
            layout.prop(settings, "result_path", text="Motion File")
            try:
                read_runtime(bpy.path.abspath(settings.runtime_root))
                layout.label(text="Local runtime configured", icon="CHECKMARK")
            except Exception:
                layout.label(text="Local runtime needs setup", icon="INFO")
            layout.label(text="First use of a prompt takes longer.")


ANIMATION_CLASSES = (
    CharacterDesignerAnimationState,
    CHARACTERDESIGNER_OT_animation_generate,
    CHARACTERDESIGNER_OT_animation_cancel,
    CHARACTERDESIGNER_OT_animation_import,
    CHARACTERDESIGNER_OT_animation_apply,
    CHARACTERDESIGNER_OT_animation_restore,
    CHARACTERDESIGNER_OT_animation_open_log,
    CHARACTERDESIGNER_PT_animation,
)
