import time

import bpy
from bpy.app.handlers import persistent


PBR_BAKE_RUNTIME_ACTIVE = False
PBR_BAKE_RUNTIME_PENDING = False
PBR_BAKE_RUNTIME_PENDING_STARTED = 0.0
PBR_BAKE_RESET_TIMER_PENDING = False
PBR_BAKE_PENDING_TIMEOUT_SECONDS = 3.0


def set_pbr_bake_runtime_active(active):
    global PBR_BAKE_RUNTIME_ACTIVE
    PBR_BAKE_RUNTIME_ACTIVE = bool(active)


def set_pbr_bake_runtime_pending(active):
    global PBR_BAKE_RUNTIME_PENDING, PBR_BAKE_RUNTIME_PENDING_STARTED
    PBR_BAKE_RUNTIME_PENDING = bool(active)
    PBR_BAKE_RUNTIME_PENDING_STARTED = time.monotonic() if active else 0.0


def pbr_bake_runtime_state_is_stale(settings):
    if settings is None or PBR_BAKE_RUNTIME_ACTIVE:
        return False
    if PBR_BAKE_RUNTIME_PENDING and time.monotonic() - PBR_BAKE_RUNTIME_PENDING_STARTED < PBR_BAKE_PENDING_TIMEOUT_SECONDS:
        return False
    status = getattr(settings, "pbr_bake_status", "") or ""
    return (
        bool(getattr(settings, "pbr_bake_running", False))
        or status == "Starting PBR bake"
        or status.startswith("PBR Bake ")
    )


def reset_pbr_bake_runtime_state(scene=None, force=False):
    set_pbr_bake_runtime_pending(False)
    set_pbr_bake_runtime_active(False)
    changed = False
    scenes = [scene] if scene is not None else list(getattr(bpy.data, "scenes", []))
    for current_scene in scenes:
        settings = getattr(current_scene, "rr_builder_export_settings", None)
        if settings is None:
            continue
        if not force and not pbr_bake_runtime_state_is_stale(settings):
            continue
        try:
            settings.pbr_bake_running = False
            settings.pbr_bake_progress = 0.0
            settings.pbr_bake_status = getattr(settings, "pbr_bake_last_summary", "") or ""
            changed = True
        except AttributeError as exc:
            if "Writing to ID classes in this context is not allowed" not in str(exc):
                raise
            request_pbr_bake_runtime_state_reset(delay=0.1)
            return False
    return changed


def request_pbr_bake_runtime_state_reset(delay=0.0):
    global PBR_BAKE_RESET_TIMER_PENDING
    if PBR_BAKE_RESET_TIMER_PENDING:
        return
    PBR_BAKE_RESET_TIMER_PENDING = True
    try:
        bpy.app.timers.register(reset_pbr_bake_runtime_state_deferred, first_interval=max(0.0, float(delay)))
    except Exception:
        PBR_BAKE_RESET_TIMER_PENDING = False


def reset_pbr_bake_runtime_state_deferred():
    global PBR_BAKE_RESET_TIMER_PENDING
    PBR_BAKE_RESET_TIMER_PENDING = False
    reset_pbr_bake_runtime_state()
    return None


@persistent
def reset_pbr_bake_runtime_state_on_load(_dummy):
    reset_pbr_bake_runtime_state()
