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
HANDLER_NAMES = (
    "load_post", "save_pre", "undo_post", "redo_post", "blend_import_post",
    "depsgraph_update_post", "render_pre",
)
MENU_NAMES = ("NODE_MT_add", "NODE_MT_context_menu")


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
        for name in HANDLER_NAMES
        if hasattr(bpy.app.handlers, name)
    }


def menu_callbacks(name):
    # Dynamic Blender menus store both their built-in draw and appended draws
    # on the replacement draw function. Inspect real callbacks, not a module's
    # own registration bookkeeping, so a leaked previous module is detected.
    menu = getattr(bpy.types, name)
    return tuple(getattr(menu.draw, "_draw_funcs", ()))


def menu_counts():
    return {
        name: tuple(sorted(
            callback.__name__ for callback in menu_callbacks(name)
            if getattr(callback, "__module__", "").startswith(ADDON_NAME)
        ))
        for name in MENU_NAMES
    }


def assert_mixer_unregistered(module):
    mixer = module.rr_shader_mixer
    check(not any(getattr(cls, "is_registered", False) for cls in mixer.CLASSES),
          "Disable leaked Mixer operator classes")
    check(not menu_counts()["NODE_MT_add"]
          and mixer.draw_context_menu not in menu_callbacks("NODE_MT_context_menu"),
          "Disable leaked Mixer menu callbacks")
    check(not any(callback in getattr(bpy.app.handlers, name)
                  for name, callback in mixer._HANDLERS),
          "Disable leaked Mixer handlers")
    check(not mixer._OWNER_TREES, "Disable retained Mixer scene references")


def legacy_ring_state(material):
    stack = material.rr_ring_stack
    return (
        stack.stack_id, stack.generated_group.as_pointer() if stack.generated_group else None,
        tuple((layer.uid, layer.name, layer.radius, layer.width, layer.softness,
               layer.enabled, layer.mode, layer.start_angle, layer.sweep_angle)
              for layer in stack.layers),
        tuple((node.name, node.bl_idname, tuple(node.location))
              for node in material.node_tree.nodes),
        tuple((link.from_node.name, link.from_socket.identifier,
               link.to_node.name, link.to_socket.identifier)
              for link in material.node_tree.links),
    )


def assert_mixer_index_restored(module, material):
    check(module.restore_shader_mixer_index_deferred() is None,
          "Normal deferred Mixer discovery did not finish")
    check(module.rr_shader_mixer._OWNER_TREES.get(material.node_tree.as_pointer()) == material.node_tree,
          "Deferred discovery did not restore a saved Mixer owner")


def keymap_count():
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return 0
    return sum(
        item.idname == "rr_builder.duplicate_move_without_group_membership"
        for keymap in keyconfig.keymaps
        for item in keymap.keymap_items
    )


def assert_registered(module, counts, keys, menus):
    check(module is not None and module.__addon_enabled__, "Standard enable failed")
    check(os.path.normcase(os.path.realpath(module.__file__)) == os.path.normcase(CANONICAL_INIT), "Loaded installed source instead of canonical source")
    check(hasattr(bpy.types.Scene, "rr_builder_export_settings"), "Scene properties missing")
    check(handler_counts() == counts, f"Handlers leaked or were not registered: {handler_counts()}")
    check(keymap_count() == keys, "Duplicate keymaps accumulated")
    check(all(bpy.app.timers.is_registered(callback) for callback in callbacks(module)), "A lifecycle timer was not registered")
    check(menu_counts() == menus, f"Mixer menus leaked or were not registered: {menu_counts()}")
    mixer = module.rr_shader_mixer
    check(all(getattr(cls, "is_registered", False) for cls in mixer.CLASSES),
          "Mixer operator classes missing")
    check(not menu_counts()["NODE_MT_add"]
          and mixer.draw_context_menu in menu_callbacks("NODE_MT_context_menu"),
          "Registered menus belong to a stale Mixer module")
    check(all(callback in getattr(bpy.app.handlers, name)
              for name, callback in mixer._HANDLERS),
          "Registered handlers belong to a stale Mixer module")
    check(hasattr(bpy.types.Material, "rr_ring_stack"), "Legacy Ring Stack data schema missing")


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
    baseline_menus = menu_counts()
    material = None
    expected_legacy = None

    try:
        # This is the real enable path; calling module.register() directly missed
        # the _RestrictData crash that this regression test protects against.
        errors = []
        with mock.patch.object(module.rr_shader_mixer, "rebuild_index",
                               side_effect=AssertionError("Register scanned shader scene data")) as discovery:
            module = addon_utils.enable(ADDON_NAME, default_set=False, refresh_handled=True, handle_error=errors.append)
        check(not discovery.called, "Restricted register scanned Mixer datablocks")
        check(not errors, f"Restricted registration errors: {errors}")
        check(module is not None, "addon_utils.enable returned None")
        check(not module.OBJECT_MANAGER_NAME_SYNC_READY, "Name baseline was not deferred")
        check(dict(root.items()) == original_root_properties, "Register wrote scene data")
        expected_handlers = {
            "load_post": tuple(sorted((
                "reset_pbr_bake_runtime_state_on_load", "repair_rr_normal_map_nodes_on_load",
                "migrate_reference_layout_usage_on_load", "reset_object_manager_duplicate_guard_on_load",
                "_on_reload",
            ))),
            "save_pre": ("_before_save_or_render", "clear_inherited_rr_identity_before_save"),
            "undo_post": ("_on_reload", "sync_object_manager_names_after_history"),
            "redo_post": ("_on_reload", "sync_object_manager_names_after_history"),
            "depsgraph_update_post": ("_on_graph_update",),
            "render_pre": ("_before_save_or_render",),
        }
        if hasattr(bpy.app.handlers, "blend_import_post"):
            expected_handlers["blend_import_post"] = ("remember_object_manager_imported_objects",)
        expected_keys = baseline_keys + len(module.OBJECT_MANAGER_DUPLICATE_KEYMAPS)
        expected_menus = {"NODE_MT_add": (),
                          "NODE_MT_context_menu": ("draw_context_menu",)}
        assert_registered(module, expected_handlers, expected_keys, expected_menus)

        with RestrictBlend():
            check(module.reset_object_manager_name_sync_state() is False, "Restricted reset must defer safely")
            check(module.sync_object_manager_names() is False, "Restricted sync must defer safely")
            check(module.scene_selection_queue_sync_timer() == 0.2, "Restricted timer must retry without scene reads")
            check(module.restore_shader_mixer_index_deferred() == 0.1,
                  "Restricted Mixer discovery must retry without scene reads")
        check(module.scene_selection_queue_sync_timer() == 0.2, "Normal first timer failed")
        check(module.OBJECT_MANAGER_NAME_SYNC_READY, "Normal timer did not initialize names")
        check(module.OBJECT_MANAGER_DUPLICATE_GUARD_READY, "Normal timer did not initialize duplicate guard")
        check(root.name == "LegacyNative" and module.object_manager_display_name(root) == "LegacyDisplay", "First enable overwrote legacy mismatch")
        check(set(bpy.data.objects.keys()) == original_names, "Registration created/deleted scene objects")

        # Confirmed edits must still work once initialization has completed.
        root.name = "CommittedNative"
        module.scene_selection_queue_sync_timer()
        check(root.name == module.object_manager_display_name(root) == member[module.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP], "Confirmed native name did not synchronize")

        # Removing the old Rings panel must not erase its saved PropertyGroups
        # or alter a material graph when the add-on is disabled/reloaded.
        material = bpy.data.materials.new("Legacy Ring Stack Material")
        material.use_nodes = True
        legacy_group = bpy.data.node_groups.new("Legacy Ring Stack", "ShaderNodeTree")
        with module.rr_ring_stack._editing(material):
            stack = material.rr_ring_stack
            stack.stack_id = "legacy_ring_stack_compatibility"
            stack.generated_group = legacy_group
            layer = stack.layers.add()
            layer.uid = "legacy_ring_layer"
            layer.name = "Original Arc"
            layer.mode = "ARC"
            layer.radius = 0.3
            layer.width = 0.08
            layer.softness = 0.01
            layer.start_angle = 15.0
            layer.sweep_angle = 120.0
        module.rr_shader_mixer.add_mix_shaders(material.node_tree)
        expected_legacy = legacy_ring_state(material)

        old_module = module
        addon_utils.disable(ADDON_NAME, default_set=False, refresh_handled=True)
        check(handler_counts() == baseline_handlers, "Disable leaked handlers")
        check(keymap_count() == baseline_keys, "Disable leaked keymaps")
        check(menu_counts() == baseline_menus, "Disable leaked Mixer menus")
        assert_mixer_unregistered(old_module)
        check(not any(bpy.app.timers.is_registered(callback) for callback in callbacks(old_module)), "Disable leaked timers")
        module = addon_utils.enable(ADDON_NAME, default_set=False, refresh_handled=True, handle_error=errors.append)
        assert_registered(module, expected_handlers, expected_keys, expected_menus)
        assert_mixer_index_restored(module, material)
        check(legacy_ring_state(material) == expected_legacy, "Re-enable lost hidden Ring Stack data")
        module.scene_selection_queue_sync_timer()
        check(root.name == module.object_manager_display_name(root) == "CommittedNative", "Re-enable lost confirmed name")

        # Run the actual Refresh Add-on callback, including its new module import.
        for _ in range(2):
            old_module = module
            check(old_module.rr_addon_reload_deferred() is None, "Refresh callback did not finish")
            module = sys.modules.get(ADDON_NAME)
            check(module is not old_module, "Refresh did not replace the module")
            assert_registered(module, expected_handlers, expected_keys, expected_menus)
            assert_mixer_unregistered(old_module)
            assert_mixer_index_restored(module, material)
            check(legacy_ring_state(material) == expected_legacy, "Refresh altered hidden Ring Stack data or material graph")
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
        assert_registered(module, expected_handlers, expected_keys, expected_menus)
        assert_mixer_unregistered(failed_modules[0])
        assert_mixer_index_restored(module, material)
        check(legacy_ring_state(material) == expected_legacy, "Failed refresh altered hidden Ring Stack data or material graph")
        check(not any(bpy.app.timers.is_registered(callback) for callback in callbacks(failed_modules[0])), "Failed refresh leaked partial new timers")
        module.scene_selection_queue_sync_timer()
        check(root.name == module.object_manager_display_name(root) == "CommittedNative", "Failed refresh altered scene name")

        # The new module registers before the main package classes. A failure
        # there leaves only the legacy schema and Mixer side effects installed,
        # which must also be removed before restoring the previous add-on.
        old_module = module
        partial_modules = []

        def fail_during_mixer_registration(name, **kwargs):
            fresh = importlib.import_module(name)
            fresh.__time__ = os.path.getmtime(fresh.__file__)
            partial_modules.append(fresh)
            actual_register = fresh.rr_shader_mixer.register

            def register_mixer_then_fail():
                actual_register()
                raise RuntimeError("Intentional partial Mixer registration failure")

            fresh.rr_shader_mixer.register = register_mixer_then_fail
            return actual_enable(name, **kwargs)

        output = io.StringIO()
        with mock.patch.object(addon_utils, "enable", side_effect=fail_during_mixer_registration):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                old_module.rr_addon_reload_deferred()
        module = sys.modules.get(ADDON_NAME)
        check(module is old_module, "Partial Mixer failure did not restore previous module")
        check("Intentional partial Mixer registration failure" in output.getvalue(),
              "Partial Mixer failure injection did not execute")
        assert_registered(module, expected_handlers, expected_keys, expected_menus)
        assert_mixer_unregistered(partial_modules[0])
        assert_mixer_index_restored(module, material)
        check(not any(bpy.app.timers.is_registered(callback) for callback in callbacks(partial_modules[0])),
              "Partial Mixer failure leaked new timers")
        check(legacy_ring_state(material) == expected_legacy,
              "Partial Mixer failure altered hidden Ring Stack data or material graph")
    finally:
        addon_utils.disable(ADDON_NAME, default_set=False, refresh_handled=True)

    check(not hasattr(bpy.types.Scene, "rr_builder_export_settings"), "Final disable leaked Scene properties")
    check(handler_counts() == baseline_handlers, "Final disable leaked handlers")
    check(keymap_count() == baseline_keys, "Final disable leaked keymaps")
    check(menu_counts() == baseline_menus, "Final disable leaked Mixer menus")
    assert_mixer_unregistered(module)
    check(not hasattr(bpy.types.Material, "rr_ring_stack"), "Final disable leaked Ring Stack schema")
    print(f"RR_ADDON_REGISTRATION_LIFECYCLE_PASS checks={CHECKS}")


if __name__ == "__main__":
    main()
