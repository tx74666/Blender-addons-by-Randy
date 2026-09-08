"""Real addon_utils enable/disable regression under Blender's RestrictBlend.

Run with --background --factory-startup --python-exit-code 1 --python this file.
Only synthetic scene data is used. No installed add-on or blend file is written.
"""
import os
import sys
import traceback

import addon_utils
import bpy
from _bpy_restrict_state import RestrictBlend


TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, os.path.join(ROOT, "addons"))
sys.path.insert(0, TESTS)
ERRORS = []


def record_error():
    ERRORS.append(traceback.format_exc())


def enable():
    module = addon_utils.enable("character_designer", default_set=False,
                                persistent=False, handle_error=record_error)
    assert module is not None and not ERRORS, "\n".join(ERRORS)
    assert os.path.samefile(os.path.dirname(module.__file__), os.path.join(ROOT, "addons", "character_designer"))
    assert addon_utils.check("character_designer")[1]
    module._validate_registration_integrity()
    from character_designer import forearm_twist as runtime
    assert runtime._RUNTIME_REGISTERED
    assert runtime._INITIALIZE_PENDING
    assert bpy.app.timers.is_registered(runtime._complete_runtime_lifecycle)
    return module, runtime


def assert_disabled(runtime):
    assert not ERRORS, "\n".join(ERRORS)
    assert not addon_utils.check("character_designer")[1]
    assert not hasattr(bpy.types.WindowManager, "character_designer_forearm_twist")
    assert not runtime._RUNTIME_REGISTERED
    for name, handler in runtime._HANDLERS:
        assert handler not in getattr(bpy.app.handlers, name)


def main():
    module, runtime = enable()
    bpy.context.scene.frame_set(2)
    assert not runtime._INITIALIZE_PENDING and runtime._SCENE_STATE_ACTIVE
    print("PASS real addon_utils.enable and first-frame initialization", flush=True)

    import test_forearm_twist_blender as fixtures
    fixture = fixtures.make_fixture("DIRECT_PREROLL")
    mesh = fixture["mesh"]
    bpy.context.scene.render.use_lock_interface = False
    runtime.start_test(bpy.context, mesh)
    runtime.finish_test(bpy.context, True)
    key = mesh.data.shape_keys.key_blocks[runtime.KEY_PREFIX + "L"]
    assert not key.mute and bpy.context.scene.render.use_lock_interface
    addon_utils.disable("character_designer", default_set=False, handle_error=record_error)
    assert_disabled(runtime)
    assert key.mute and not bpy.context.scene.render.use_lock_interface
    assert not bpy.app.timers.is_registered(runtime._complete_runtime_lifecycle)
    print("PASS real disable restores scene and removes runtime", flush=True)

    module, runtime = enable()
    bpy.context.scene.frame_set(3)
    assert not runtime._INITIALIZE_PENDING and not key.mute
    assert bpy.context.scene.render.use_lock_interface
    assert runtime._KEY_REFERENCES
    for name, handler in runtime._HANDLERS:
        assert getattr(bpy.app.handlers, name).count(handler) == 1
    print("PASS real re-enable with existing calibration", flush=True)

    # This is Blender's actual restricted context, not a mock. It also covers
    # unregister() called during enable() failure rollback with live scene state.
    with RestrictBlend():
        addon_utils.disable("character_designer", default_set=False, handle_error=record_error)
        assert not hasattr(bpy.data, "objects")
    assert_disabled(runtime)
    assert runtime._SCENE_CLEANUP_PENDING
    assert bpy.app.timers.is_registered(runtime._complete_runtime_lifecycle)
    # Background script execution does not pump GUI timers. Invoke the actual
    # registered callback after leaving RestrictBlend to check its scene work.
    assert runtime._complete_runtime_lifecycle() is None
    assert key.mute and not bpy.context.scene.render.use_lock_interface
    assert not runtime._SCENE_CLEANUP_PENDING
    print("PASS restricted disable and deferred scene cleanup", flush=True)

    module, runtime = enable()
    # Failed registration must not leave an initialization callback from a
    # partially unregistered module. Disable before any scene initialization.
    with RestrictBlend():
        addon_utils.disable("character_designer", default_set=False, handle_error=record_error)
    assert_disabled(runtime)
    assert not runtime._INITIALIZE_PENDING and not runtime._SCENE_CLEANUP_PENDING
    assert not bpy.app.timers.is_registered(runtime._complete_runtime_lifecycle)
    print("PASS restricted rollback before initialization cancels timer", flush=True)

    module, runtime = enable()
    bpy.context.scene.frame_set(4)
    assert not runtime._INITIALIZE_PENDING and not key.mute
    module._validate_registration_integrity()
    addon_utils.disable("character_designer", default_set=False, handle_error=record_error)
    assert_disabled(runtime)
    assert not bpy.app.timers.is_registered(runtime._complete_runtime_lifecycle)
    print("PASS final re-enable and full cleanup", flush=True)


if __name__ == "__main__":
    main()
