"""Real WM modal bake events in a disposable factory GUI Blender child.

The caller launches this file (never from an artist session), for example with
--factory-startup --disable-autoexec --no-window-focus --python <this file>
-- --report <absolute JSON path> --sandbox <unique Validation child directory>
[--test-esc] [--deadline 8]. Do not use -b. Before launch the caller must create
the sandbox and set only this child's TEMP/TMP environment to that directory.
--test-esc additionally requires Blender's --enable-event-simulate startup flag.
The caller must hide the child window and retain an external process timeout:
the cooperative app-timer watchdog cannot interrupt a blocked native solver.

Observers forward native invoke/modal/advance/finish methods without fabricated
Event objects or manual modal calls. One 35 ms pause after a real frame creates
late timer wakes. No artist file is opened, appended, read, or saved.
"""

import argparse
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_ROOT = Path(r"D:\Blender\Projects\Character\X\Validation")
sys.path.insert(0, str(ROOT / "addons"))
from character_designer import skirt, skirt_physics, skirt_rig


def arguments():
    tail = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--sandbox", required=True, type=Path,
                        help="Unique existing Validation directory used by this child's TEMP and TMP")
    parser.add_argument("--deadline", type=float, default=8.0)
    parser.add_argument("--test-esc", action="store_true")
    args = parser.parse_args(tail)
    if not args.report.is_absolute():
        parser.error("--report must be an absolute local JSON path")
    if args.report.suffix.lower() != ".json":
        parser.error("--report must end in .json")
    if not args.sandbox.is_absolute() or not args.sandbox.is_dir():
        parser.error("--sandbox must be an absolute existing directory")
    if args.sandbox.resolve() == VALIDATION_ROOT.resolve() \
            or not args.sandbox.resolve().is_relative_to(VALIDATION_ROOT.resolve()):
        parser.error("--sandbox must be a dedicated child directory within X/Validation")
    if not 5.0 <= args.deadline <= 10.0:
        parser.error("--deadline must be between 5 and 10 seconds")
    return args


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def native_timer_state(timer):
    if timer is None:
        return {"present": False}
    try:
        return {"present": True, "valid": bool(timer.as_pointer()),
                "time_step": timer.time_step}
    except (ReferenceError, RuntimeError) as exc:
        return {"present": True, "valid": False, "invalidated": type(exc).__name__}


def event_state(event):
    return {"python_type": type(event).__name__, "rna_type": event.bl_rna.identifier,
            "type": event.type, "value": event.value, "has_timer": hasattr(event, "timer")}


def fixture(context):
    """112-vertex native Cloth cage; no imported model or external fixture."""
    bpy.ops.object.select_all(action="DESELECT")
    data = bpy.data.armatures.new("GUI Bake Test Character")
    rig = bpy.data.objects.new("GUI Bake Test Character", data)
    context.scene.collection.objects.link(rig)
    rig.select_set(True)
    context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    hips = data.edit_bones.new("Hips")
    hips.head, hips.tail = (0, 0, 2), (0, 0, 2.25)
    for side, x in (("L", 0.2), ("R", -0.2)):
        bone = data.edit_bones.new("thigh." + side)
        bone.head, bone.tail, bone.parent = (x, 0, 1.9), (x, 0, 1.1), hips
    bpy.ops.object.mode_set(mode="OBJECT")
    vertices, faces, sides, rows = [], [], 16, 5
    for row in range(rows):
        depth = row / (rows - 1)
        radius = 0.5 + depth * 0.5
        for col in range(sides):
            angle = math.tau * col / sides
            vertices.append((radius * math.cos(angle), 0.8 * radius * math.sin(angle), 2 - depth))
    for row in range(rows - 1):
        for col in range(sides):
            following = (col + 1) % sides
            faces.append((row * sides + col, (row + 1) * sides + col,
                          (row + 1) * sides + following, row * sides + following))
    mesh = bpy.data.meshes.new("GUI Bake Test Dress Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    source = bpy.data.objects.new("GUI Bake Test Dress", mesh)
    context.scene.collection.objects.link(source)
    rig.select_set(False)
    source.select_set(True)
    context.view_layer.objects.active = source
    skirt_rig.build_skirt(context, source, chain_count=4, segment_count=2,
                          armature=rig, parent_bone="Hips")
    skirt_physics.add_physics(context, source)
    _, _, proxy, cloth = skirt_physics.validate_physics(source)
    require(len(proxy.data.vertices) == 112, "Unexpected fixture cage size")
    return source, cloth


class Harness:
    def __init__(self, args):
        self.args = args
        self.deadline = time.monotonic() + args.deadline
        self.window = self.area = self.region = self.source = self.cloth = None
        self.extra_timer = None
        self.registered = []
        self.originals = {}
        self.current = None
        self.started = False
        self.done = False
        startup = sys.argv[:sys.argv.index("--")] if "--" in sys.argv else sys.argv
        self.report = {
            "ok": False, "runtime": {"blender": bpy.app.version_string,
                "binary": bpy.app.binary_path, "source": skirt.__file__,
                "background": bpy.app.background, "factory_startup": "--factory-startup" in startup,
                "artist_filepath_empty": not bpy.data.filepath},
            "deadline_seconds": args.deadline, "cases": [], "errors": [],
            "event_simulate": {"requested": args.test_esc,
                "startup_enabled": "--enable-event-simulate" in startup,
                "rna_available": "event_simulate" in bpy.types.Window.bl_rna.functions},
            "sources": {
                "cli": "https://docs.blender.org/manual/en/5.1/advanced/command_line/arguments.html",
                "window_api": "https://github.com/blender/blender/blob/v5.1.0/source/blender/makesrna/intern/rna_wm_api.cc#L662",
                "native_timer_removal": "https://github.com/blender/blender/blob/v5.1.0/source/blender/windowmanager/intern/wm_window.cc#L2381",
                "native_exit": "https://github.com/blender/blender/blob/v5.1.0/source/blender/windowmanager/intern/wm_init_exit.cc#L439",
                "native_temp_environment": "https://github.com/blender/blender/blob/v5.1.0/source/blender/blenlib/intern/tempfile.cc#L53",
                "native_temp_session": "https://github.com/blender/blender/blob/v5.1.0/source/blender/blenkernel/intern/appdir.cc#L1129",
                "preferences_save_rna": "https://github.com/blender/blender/blob/v5.1.0/source/blender/makesrna/intern/rna_userdef.cc#L6853",
                "skip_preferences_save_api": "https://github.com/blender/blender/blob/v5.1.0/source/blender/python/intern/bpy_app.cc#L612"},
        }

    def check_exit_sandbox(self):
        sandbox = self.args.sandbox.resolve()
        env_paths = {name: Path(os.environ[name]).resolve() if os.environ.get(name) else None
                     for name in ("TEMP", "TMP")}
        native_temp = Path(bpy.app.tempdir).resolve()
        temporary_preference = bpy.context.preferences.filepaths.temporary_directory
        self.report["exit_sandbox"] = {
            "path": str(sandbox), "environment": {name: str(path) if path else None
                for name, path in env_paths.items()}, "native_session_tempdir": str(native_temp),
            "temporary_directory_preference": temporary_preference,
            "native_tempdir_inside_sandbox": native_temp.is_relative_to(sandbox),
            "expected_quit_file": str(sandbox / "quit.blend"),
            "quit_recovery_disabled": False,
            "protection": "Native recovery writes stay in this child's sandbox; no global file is inspected or restored",
        }
        require(all(path == sandbox for path in env_paths.values()), "Child TEMP/TMP do not match --sandbox")
        require(native_temp.is_dir() and native_temp.is_relative_to(sandbox),
                "Native bpy.app.tempdir escaped the child's sandbox")
        require(not temporary_preference or Path(temporary_preference).resolve() == sandbox,
                "Native preference overrides the isolated TEMP base")
        self.report["exit_sandbox"]["checked"] = True

    def protect_preferences(self):
        preferences = bpy.context.preferences
        prop = preferences.bl_rna.properties.get("use_preferences_save")
        require(prop is not None and prop.type == "BOOLEAN" and not prop.is_readonly,
                "Verified native Preferences.use_preferences_save is unavailable")
        require(hasattr(bpy.app, "use_userpref_skip_save_on_exit"),
                "Verified native bpy.app.use_userpref_skip_save_on_exit is unavailable")
        self.report["temporary_preferences"] = {
            "original_use_preferences_save": preferences.use_preferences_save,
            "original_skip_save_on_exit": bpy.app.use_userpref_skip_save_on_exit,
            "preferences_saved": False, "scope": "Dedicated child process memory only",
        }
        # These are preference-file guards, not a quit.blend switch. 5.1 has
        # no RNA switch for its unconditional normal-GUI exit recovery write.
        preferences.use_preferences_save = False
        bpy.app.use_userpref_skip_save_on_exit = True
        self.report["temporary_preferences"].update(
            effective_use_preferences_save=preferences.use_preferences_save,
            effective_skip_save_on_exit=bpy.app.use_userpref_skip_save_on_exit)
        require(not preferences.use_preferences_save and bpy.app.use_userpref_skip_save_on_exit,
                "Native preference save-on-exit protection did not take effect")
        # Keep these guards until process exit. Never save or restore preferences
        # before quit; restoring them would reenable a global preference write.

    def override(self):
        return bpy.context.temp_override(window=self.window, area=self.area, region=self.region)

    def prepare(self):
        wm = bpy.context.window_manager
        require(len(wm.windows) == 1, "Expected exactly one disposable factory GUI window")
        self.window = wm.windows[0]
        self.area = next((area for area in self.window.screen.areas if area.type == "VIEW_3D"), None)
        require(self.area is not None, "Factory child has no VIEW_3D area")
        self.region = next((region for region in self.area.regions if region.type == "WINDOW"), None)
        require(self.region is not None, "Factory VIEW_3D has no WINDOW region")
        self.report["context"] = {"startup_area": getattr(bpy.context.area, "type", None),
            "window_pointer": self.window.as_pointer(), "screen": self.window.screen.name,
            "area_type": self.area.type, "region_type": self.region.type}
        cls = skirt.CHARACTERDESIGNER_OT_skirt_bake_physics
        require(not cls.is_registered, "Bake class already registered; use a clean factory child")
        require(not hasattr(bpy.types.WindowManager, "character_designer_skirt"),
                "Skirt state already registered; use a clean factory child")
        self.install_observers(cls)
        harness = self

        class WM_OT_skirt_bake_timer_probe(bpy.types.Operator):
            bl_idname = "wm.skirt_bake_timer_probe"
            bl_label = "Disposable Skirt Bake Timer Probe"
            bl_options = {"INTERNAL"}

            def invoke(self, context, _event):
                context.window_manager.modal_handler_add(self)
                return {"RUNNING_MODAL"}

            def modal(self, _context, event):
                if harness.done:
                    return {"CANCELLED"}
                case = harness.current
                if case is not None and case.get("quiet_until") and event.type == "TIMER":
                    case["quiet_timer_events"].append(event_state(event))
                return {"PASS_THROUGH"}

        for item in (skirt.CharacterDesignerSkirtState, cls, WM_OT_skirt_bake_timer_probe):
            require(not item.is_registered, "Fixture class already registered")
            bpy.utils.register_class(item)
            self.registered.append(item)
        bpy.types.WindowManager.character_designer_skirt = bpy.props.PointerProperty(
            type=skirt.CharacterDesignerSkirtState)
        with self.override():
            self.source, self.cloth = fixture(bpy.context)
            wm.character_designer_skirt.source = self.source
            require(bpy.ops.wm.skirt_bake_timer_probe("INVOKE_DEFAULT") == {"RUNNING_MODAL"},
                    "Native timer cleanup probe did not enter modal handling")
            self.start_case("finish", end=8)

    def install_observers(self, cls):
        for name in ("invoke", "modal", "_advance", "_finish"):
            self.originals[name] = (name in cls.__dict__, getattr(cls, name))
        original_invoke = self.originals["invoke"][1]
        original_modal = self.originals["modal"][1]
        original_advance = self.originals["_advance"][1]
        original_finish = self.originals["_finish"][1]
        harness = self

        def invoke(operator, context, event):
            harness.current["invoke_event"] = event_state(event)
            harness.current["invoke_context"] = {
                "window_matches": context.window == harness.window,
                "area": getattr(context.area, "type", None),
                "region": getattr(context.region, "type", None)}
            return original_invoke(operator, context, event)

        def modal(operator, context, event):
            case = harness.current
            observed = event_state(event)
            observed.update(at=time.monotonic(), before_deadline=operator._next_step_time,
                            window_matches=context.window == harness.window)
            case["events"].append(observed)
            before = len(case["advances"])
            case["inside_modal"] = True
            try:
                result = original_modal(operator, context, event)
                observed["result"] = sorted(result)
                if "FINISHED" in result or "CANCELLED" in result:
                    case["terminal"] = sorted(result)
                return result
            except Exception:
                case["exceptions"].append(traceback.format_exc())
                raise
            finally:
                case["inside_modal"] = False
                observed["advances"] = len(case["advances"]) - before

        def advance(operator, context):
            case = harness.current
            observed = {"begin": time.monotonic(), "modal": case["inside_modal"]}
            case["advances"].append(observed)
            try:
                result = original_advance(operator, context)
                observed["result"] = None if result is None else sorted(result)
                if result is None:
                    observed["frame"] = context.scene.frame_current
                    observed["message"] = context.window_manager.character_designer_skirt.last_message
                    case["frames"].append(context.scene.frame_current)
                    if case["name"] == "finish" and len(case["frames"]) == 2:
                        # Real queued WM TIMER events arrive after this slow step.
                        time.sleep(0.035)
                        case["injected_delay_seconds"] = 0.035
                return result
            except Exception:
                case["exceptions"].append(traceback.format_exc())
                raise
            finally:
                observed["end"] = time.monotonic()

        def finish(operator):
            case = harness.current
            timer = operator._timer
            observed = {"native_timer_before": native_timer_state(timer)}
            try:
                return original_finish(operator)
            finally:
                # 5.1 tags removal now and frees at a later WM iteration. RNA
                # validity is not a timer-membership API; never probe a freed ref.
                observed.update(native_timer_after={"dereferenced": False, "removal_deferred": True},
                    cleared={name: getattr(operator, name) is None
                             for name in ("_timer", "_next_step_time", "_wm", "_steps", "_bake_source")},
                    registry_empty=not skirt._ACTIVE_BAKES)
                case["cleanup"].append(observed)

        cls.invoke, cls.modal, cls._advance, cls._finish = invoke, modal, advance, finish

    def start_case(self, name, end):
        settings = bpy.context.window_manager.character_designer_skirt
        settings.use_scene_range = False
        settings.bake_start, settings.bake_end = 1, end
        bpy.context.scene.frame_set(3)
        self.current = {"name": name, "range": [1, end], "saved_frame": 3,
            "events": [], "advances": [], "frames": [], "exceptions": [], "cleanup": [],
            "inside_modal": False, "started": time.monotonic(), "terminal": None,
            "extra_timer_interval": 0.002, "own_timer_interval": 0.01}
        self.report["cases"].append(self.current)
        self.extra_timer = bpy.context.window_manager.event_timer_add(0.002, window=self.window)
        self.current["extra_timer_before"] = native_timer_state(self.extra_timer)
        require(bpy.ops.character_designer.skirt_bake_physics.poll(), "Native bake poll failed")
        result = bpy.ops.character_designer.skirt_bake_physics("INVOKE_DEFAULT")
        self.current["invoke_result"] = sorted(result)
        require(result == {"RUNNING_MODAL"}, "Native invocation did not enter WM modal handling")
        require(len(skirt._ACTIVE_BAKES) == 1, "Native invocation was not registered as active")

    def remove_extra_timer(self):
        if self.extra_timer is None:
            return
        timer, self.extra_timer = self.extra_timer, None
        bpy.context.window_manager.event_timer_remove(timer)
        if self.current is not None:
            self.current["extra_timer_remove_returned"] = True
            self.current["extra_timer_after"] = {"dereferenced": False, "removal_deferred": True}

    def validate_case(self):
        case = self.current
        self.remove_extra_timer()
        case["sealed_cache"] = self.cloth.point_cache.is_baked
        case["baked_range"] = skirt_rig.read_record(self.source)["physics"]["baked_range"]
        case["restored_frame"] = bpy.context.scene.frame_current
        case["registry_empty"] = not skirt._ACTIVE_BAKES
        case["native_modal_operators"] = [operator.bl_rna.identifier for operator in self.window.modal_operators]
        require("CHARACTERDESIGNER_OT_skirt_bake_physics" not in case["native_modal_operators"],
                "Native WM retained the completed bake modal handler")
        require(not case["exceptions"], "Native modal callback raised")
        require(case["invoke_context"] == {"window_matches": True, "area": "VIEW_3D", "region": "WINDOW"},
                "Bake invocation did not use the exact native VIEW_3D window/region")
        timers = [event for event in case["events"] if event["type"] == "TIMER"]
        require(timers, "No real WM TIMER event reached modal")
        require(all(event["rna_type"] == "Event" and not event["has_timer"] for event in timers),
                "Native TIMER Event capability differed from the regression premise")
        require(all(event["window_matches"] and event["advances"] <= 1 for event in case["events"]),
                "Wrong window or more than one advance per native Event")
        require(any(event["advances"] == 0 for event in timers), "No foreign/early TIMER wake was throttled")
        for previous, current in zip(case["advances"], case["advances"][1:]):
            require(current["begin"] - previous["end"] >= 0.01 - 0.0003,
                    "Timer advancement caught up or advanced faster than its interval")
        require(case["frames"] == list(range(1, len(case["frames"]) + 1)),
                "Bake skipped, repeated, or reordered native scene frames")
        require(case["cleanup"] and all(case["cleanup"][-1]["cleared"].values()),
                "Production finish retained timer, deadline, WM, steps, or source")
        require(case["registry_empty"] and case["cleanup"][-1]["registry_empty"], "Bake registry leaked")
        require(case["restored_frame"] == case["saved_frame"], "Bake did not restore the caller's frame")
        case["timer_events"] = len(timers)
        case["throttled_timer_events"] = sum(event["advances"] == 0 for event in timers)
        if case["name"] == "finish":
            require(case["terminal"] == ["FINISHED"], "Native modal bake did not finish")
            require(case["frames"] == list(range(1, 9)), "Finished bake omitted frames")
            require(case["sealed_cache"] and case["baked_range"] == [1, 8], "Sequential cache was not sealed")
            require(self.cloth.point_cache.frame_step == 1, "Sealed cache changed frame step")
        else:
            require(case.get("esc_sent") and any(event["type"] == "ESC" for event in case["events"]),
                    "Simulated native ESC did not reach modal")
            require(case["terminal"] == ["CANCELLED"], "Native ESC did not cancel")
            require(not case["sealed_cache"] and case["baked_range"] is None, "Cancelled bake claimed a sealed range")
        # Factory child has no other plain TIMER producers. The probe receives
        # native events after Bake Physics releases them and both timers are
        # removed; a surviving 2 ms or 10 ms timer must wake it during this span.
        case["quiet_timer_events"] = []
        case["quiet_until"] = time.monotonic() + 0.035
        case["validated"] = True

    def cleanup(self):
        if self.registered:
            skirt.stop_skirt_runtime()
        self.remove_extra_timer()
        self.report["cleanup_registry_empty"] = not skirt._ACTIVE_BAKES
        if self.registered and hasattr(bpy.types.WindowManager, "character_designer_skirt"):
            del bpy.types.WindowManager.character_designer_skirt
        for item in reversed(self.registered):
            bpy.utils.unregister_class(item)
        self.registered.clear()
        cls = skirt.CHARACTERDESIGNER_OT_skirt_bake_physics
        for name, (was_local, method) in self.originals.items():
            if was_local:
                setattr(cls, name, method)
            else:
                delattr(cls, name)
        self.originals.clear()
        self.report["cleanup_operator_unregistered"] = not cls.is_registered

    def finish(self, error=None):
        if self.done:
            return
        self.done = True
        if error:
            self.report["errors"].append(error)
        try:
            if self.window is not None:
                with self.override():
                    self.cleanup()
        except Exception:
            self.report["errors"].append("Cleanup: " + traceback.format_exc())
        self.report["ok"] = not self.report["errors"] and bool(self.report["cases"]) \
            and all(case.get("ok", False) for case in self.report["cases"])
        self.report["completed_at_seconds"] = time.monotonic() - (self.deadline - self.args.deadline)
        safe_exit = False
        try:
            self.check_exit_sandbox()
            require(not bpy.context.preferences.use_preferences_save
                    and bpy.app.use_userpref_skip_save_on_exit,
                    "Child preference-file exit guards were changed")
            safe_exit = True
        except Exception:
            self.report["errors"].append("Normal quit refused: " + traceback.format_exc())
            self.report["ok"] = False
        self.report["quit_requested"] = safe_exit
        try:
            self.args.report.parent.mkdir(parents=True, exist_ok=True)
            self.args.report.write_text(json.dumps(self.report, indent=2, allow_nan=False), encoding="utf-8")
            print("SKIRT_BAKE_GUI_REPORT=" + str(self.args.report), flush=True)
            print("SKIRT_BAKE_GUI_OK=" + str(self.report["ok"]), flush=True)
        finally:
            # EXEC_DEFAULT avoids a save-confirmation dialog for the disposable scene.
            if safe_exit:
                bpy.ops.wm.quit_blender("EXEC_DEFAULT")
            else:
                print("SKIRT_BAKE_GUI_QUIT_REFUSED: terminate only this dedicated child", flush=True)

    def tick(self):
        try:
            require(time.monotonic() < self.deadline, "GUI bake watchdog deadline reached")
            if not self.started:
                self.started = True
                self.prepare()
            else:
                with self.override():
                    case = self.current
                    if case["name"] == "esc" and not case.get("esc_sent") and len(case["frames"]) >= 2:
                        event = self.window.event_simulate("ESC", "PRESS",
                            x=self.region.x + self.region.width // 2,
                            y=self.region.y + self.region.height // 2)
                        case["esc_sent"] = event_state(event)
                    if not skirt._ACTIVE_BAKES:
                        if not case.get("validated"):
                            self.validate_case()
                        elif time.monotonic() >= case["quiet_until"]:
                            require(not case["quiet_timer_events"], "Removed native timers kept waking the GUI window")
                            case["ok"] = True
                            gate = self.report["event_simulate"]
                            if case["name"] == "finish" and gate["requested"] \
                                    and gate["startup_enabled"] and gate["rna_available"]:
                                skirt_physics.reset_simulation(bpy.context, self.source)
                                self.start_case("esc", end=40)
                            else:
                                if case["name"] == "finish":
                                    gate["skipped"] = "Not requested" if not gate["requested"] \
                                        else "Requires --enable-event-simulate and native RNA method"
                                self.finish()
                                return None
        except Exception:
            self.finish(traceback.format_exc())
            return None
        return 0.002


def main():
    try:
        args = arguments()
        harness = Harness(args)
    except (SystemExit, Exception):
        # Catch argparse's SystemExit too: --python-exit-code must not turn a
        # malformed child launch into an unprotected normal GUI exit.
        print("SKIRT_BAKE_GUI_ARGUMENTS_FAILED: terminate only this dedicated child", flush=True)
        return
    try:
        runtime = harness.report["runtime"]
        # Refuse mutation and quit if mistakenly executed in an artist session.
        require(runtime["factory_startup"] and runtime["artist_filepath_empty"] and not runtime["background"],
                "This script only runs in an unsaved --factory-startup GUI child")
        harness.check_exit_sandbox()
        require(not (harness.args.sandbox / "quit.blend").exists(), "Sandbox was reused; choose a fresh child directory")
        harness.protect_preferences()
    except Exception:
        # Do not raise into --python-exit-code: a normal CLI error exit can write
        # quit.blend outside the sandbox. Leave this dedicated child for the
        # caller's process watchdog, with no scene setup or modal handler.
        harness.report["errors"].append("Startup sandbox preflight: " + traceback.format_exc())
        harness.report["quit_requested"] = False
        harness.args.report.parent.mkdir(parents=True, exist_ok=True)
        harness.args.report.write_text(json.dumps(harness.report, indent=2), encoding="utf-8")
        print("SKIRT_BAKE_GUI_PREFLIGHT_FAILED: terminate only this dedicated child", flush=True)
        return
    bpy.app.timers.register(harness.tick, first_interval=0.02)


if __name__ == "__main__":
    main()
