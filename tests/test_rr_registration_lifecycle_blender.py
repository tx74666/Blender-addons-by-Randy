"""Exercise Blender's restricted addon_utils registration and real refresh path."""

import contextlib
import importlib
import io
import os
import sys
from unittest import mock

import addon_utils
import bpy
from _bpy_restrict_state import RestrictBlend


ADDON_NAME = "random_realm_builder_exporter"
ADDONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "addons")
CANONICAL_INIT = os.path.realpath(os.path.join(ADDONS_DIR, ADDON_NAME, "__init__.py"))
CHECKS = 0


def check(condition, message):
    global CHECKS
    if not condition:
        raise AssertionError(message)
    CHECKS += 1


def callbacks(module):
    return (
        module.scene_selection_queue_sync_timer,
        module.rr_addon_change_watch_deferred,
        *(callback for callback, _interval in module.RR_STARTUP_DEFERRED_TIMERS),
    )


def handler_counts():
    return {
        name: tuple(sorted(
            callback.__name__
            for callback in getattr(bpy.app.handlers, name)
            if getattr(callback, "__module__", "").startswith(ADDON_NAME)
        ))
        for name in ("load_post", "save_pre", "undo_post", "redo_post", "blend_import_post")
        if hasattr(bpy.app.handlers, name)
    }


def keymap_count():
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return 0
    return sum(
        item.idname == "rr_builder.duplicate_move_without_group_membership"
        for keymap in keyconfig.keymaps
        for item in keymap.keymap_items
    )


def assert_registered(module, counts, keys):
    check(module is not None and module.__addon_enabled__, "Standard enable failed")
    check(os.path.normcase(os.path.realpath(module.__file__)) == os.path.normcase(CANONICAL_INIT), "Loaded installed source instead of canonical source")
    check(hasattr(bpy.types.Scene, "rr_builder_export_settings"), "Scene properties missing")
    check(handler_counts() == counts, f"Handlers leaked or were not registered: {handler_counts()}")
    check(keymap_count() == keys, "Duplicate keymaps accumulated")
    check(all(bpy.app.timers.is_registered(callback) for callback in callbacks(module)), "A lifecycle timer was not registered")


def main():
    check(not hasattr(bpy.types.Scene, "rr_builder_export_settings"), "Run with --factory-startup in a separate process")
    sys.path.insert(0, ADDONS_DIR)
    for name in list(sys.modules):
        if name == ADDON_NAME or name.startswith(ADDON_NAME + "."):
            del sys.modules[name]
    module = importlib.import_module(ADDON_NAME)

    # A legacy mismatch must not be interpreted as a new edit when enabling.
    root = bpy.data.objects.new("LegacyNative", None)
    bpy.context.scene.collection.objects.link(root)
    root[module.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    root[module.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "registration_legacy_group"
    root[module.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = "LegacyDisplay"
    member = bpy.data.objects.new("LegacyMember", None)
    bpy.context.scene.collection.objects.link(member)
    member.parent = root
    member[module.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = root.name
    member[module.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = root[module.OBJECT_MANAGER_ASSEMBLY_ID_PROP]
    original_names = set(bpy.data.objects.keys())
    original_root_properties = dict(root.items())
    baseline_handlers = handler_counts()
    baseline_keys = keymap_count()

    try:
        # This is the real enable path; calling module.register() directly missed
        # the _RestrictData crash that this regression test protects against.
        errors = []
        module = addon_utils.enable(ADDON_NAME, default_set=False, refresh_handled=True, handle_error=errors.append)
        check(not errors, f"Restricted registration errors: {errors}")
        check(module is not None, "addon_utils.enable returned None")
        check(not module.OBJECT_MANAGER_NAME_SYNC_READY, "Name baseline was not deferred")
        check(dict(root.items()) == original_root_properties, "Register wrote scene data")
        expected_handlers = {
            "load_post": tuple(sorted((
                "reset_pbr_bake_runtime_state_on_load", "repair_rr_normal_map_nodes_on_load",
                "migrate_reference_layout_usage_on_load", "reset_object_manager_duplicate_guard_on_load",
            ))),
            "save_pre": ("clear_inherited_rr_identity_before_save",),
            "undo_post": ("sync_object_manager_names_after_history",),
            "redo_post": ("sync_object_manager_names_after_history",),
        }
        if hasattr(bpy.app.handlers, "blend_import_post"):
            expected_handlers["blend_import_post"] = ("remember_object_manager_imported_objects",)
        expected_keys = baseline_keys + len(module.OBJECT_MANAGER_DUPLICATE_KEYMAPS)
        assert_registered(module, expected_handlers, expected_keys)

        with RestrictBlend():
            check(module.reset_object_manager_name_sync_state() is False, "Restricted reset must defer safely")
            check(module.sync_object_manager_names() is False, "Restricted sync must defer safely")
            check(module.scene_selection_queue_sync_timer() == 0.2, "Restricted timer must retry without scene reads")
        check(module.scene_selection_queue_sync_timer() == 0.2, "Normal first timer failed")
        check(module.OBJECT_MANAGER_NAME_SYNC_READY, "Normal timer did not initialize names")
        check(module.OBJECT_MANAGER_DUPLICATE_GUARD_READY, "Normal timer did not initialize duplicate guard")
        check(root.name == "LegacyNative" and module.object_manager_display_name(root) == "LegacyDisplay", "First enable overwrote legacy mismatch")
        check(set(bpy.data.objects.keys()) == original_names, "Registration created/deleted scene objects")

        # Confirmed edits must still work once initialization has completed.
        root.name = "CommittedNative"
        module.scene_selection_queue_sync_timer()
        check(root.name == module.object_manager_display_name(root) == member[module.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP], "Confirmed native name did not synchronize")

        old_module = module
        addon_utils.disable(ADDON_NAME, default_set=False, refresh_handled=True)
        check(handler_counts() == baseline_handlers, "Disable leaked handlers")
        check(keymap_count() == baseline_keys, "Disable leaked keymaps")
        check(not any(bpy.app.timers.is_registered(callback) for callback in callbacks(old_module)), "Disable leaked timers")
        module = addon_utils.enable(ADDON_NAME, default_set=False, refresh_handled=True, handle_error=errors.append)
        assert_registered(module, expected_handlers, expected_keys)
        module.scene_selection_queue_sync_timer()
        check(root.name == module.object_manager_display_name(root) == "CommittedNative", "Re-enable lost confirmed name")

        # Run the actual Refresh Add-on callback, including its new module import.
        for _ in range(2):
            old_module = module
            check(old_module.rr_addon_reload_deferred() is None, "Refresh callback did not finish")
            module = sys.modules.get(ADDON_NAME)
            check(module is not old_module, "Refresh did not replace the module")
            assert_registered(module, expected_handlers, expected_keys)
            check(not any(bpy.app.timers.is_registered(callback) for callback in callbacks(old_module)), "Refresh leaked old timers")
            module.scene_selection_queue_sync_timer()
            check(root.name == module.object_manager_display_name(root) == "CommittedNative", "Refresh lost confirmed name")

        # Reproduce a late register failure to verify cleanup before old-module
        # rollback. Blender deletes the failed module before enable returns None.
        old_module = module
        actual_enable = addon_utils.enable
        failed_modules = []

        def fail_after_registration(name, **kwargs):
            fresh = importlib.import_module(name)
            fresh.__time__ = os.path.getmtime(fresh.__file__)
            failed_modules.append(fresh)
            actual_register = fresh.register

            def register_then_fail():
                actual_register()
                raise RuntimeError("Intentional registration lifecycle failure")

            fresh.register = register_then_fail
            return actual_enable(name, **kwargs)

        output = io.StringIO()
        with mock.patch.object(addon_utils, "enable", side_effect=fail_after_registration):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                old_module.rr_addon_reload_deferred()
        module = sys.modules.get(ADDON_NAME)
        check(module is old_module, "Failed refresh did not restore previous module")
        check("Intentional registration lifecycle failure" in output.getvalue(), "Failure injection did not execute")
        assert_registered(module, expected_handlers, expected_keys)
        check(not any(bpy.app.timers.is_registered(callback) for callback in callbacks(failed_modules[0])), "Failed refresh leaked partial new timers")
        module.scene_selection_queue_sync_timer()
        check(root.name == module.object_manager_display_name(root) == "CommittedNative", "Failed refresh altered scene name")
    finally:
        addon_utils.disable(ADDON_NAME, default_set=False, refresh_handled=True)

    check(not hasattr(bpy.types.Scene, "rr_builder_export_settings"), "Final disable leaked Scene properties")
    check(handler_counts() == baseline_handlers, "Final disable leaked handlers")
    check(keymap_count() == baseline_keys, "Final disable leaked keymaps")
    print(f"RR_ADDON_REGISTRATION_LIFECYCLE_PASS checks={CHECKS}")


if __name__ == "__main__":
    main()
