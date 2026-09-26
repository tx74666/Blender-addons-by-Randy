"""Run with Blender --background --factory-startup --python-exit-code 1."""

import importlib
import os
import sys
import tempfile
from unittest import mock

import addon_utils
import bpy


ADDON_NAME = "random_realm_builder_exporter"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "addons"))
CHECKS = 0


def check(condition, message):
    global CHECKS
    if not condition:
        raise AssertionError(message)
    CHECKS += 1


def make_object(name, collection=None, mesh=False):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name + "Mesh") if mesh else None)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def select_only(obj):
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def clear_scene(rr):
    bpy.context.scene.rr_builder_export_settings.export_queue.clear()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    rr.reset_object_manager_duplicate_guard()
    rr.reset_object_manager_name_sync_state()
    rr.reset_scene_selection_queue_lookup()


def make_group(rr, name, collection=None):
    root = make_object(name, collection)
    root[rr.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    root[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly-" + name
    root[rr.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = name
    root[rr.EXPORT_STABLE_ID_PROP] = "export-" + name
    child = make_object(name + "Child", collection, mesh=True)
    child.parent = root
    child[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = root[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP]
    child[rr.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = root.name
    return root, child


def append_collection(path):
    with bpy.data.libraries.load(path, link=False) as (source, target):
        target.collections = ["IdentityFixture"]
    collection = target.collections[0]
    bpy.context.scene.collection.children.link(collection)
    return collection


def test_identity(rr, directory):
    clear_scene(rr)
    collection = bpy.data.collections.new("IdentityFixture")
    bpy.context.scene.collection.children.link(collection)
    root, child = make_group(rr, "AppendRoot", collection)
    path = os.path.join(directory, "append_fixture.blend")
    bpy.data.libraries.write(path, {collection})
    for obj in list(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(collection)
    rr.reset_object_manager_duplicate_guard()
    rr.reset_object_manager_name_sync_state()
    imported = append_collection(path)
    root = next(obj for obj in imported.objects if rr.is_object_manager_assembly_root(obj))
    child = next(obj for obj in imported.objects if obj != root)
    rr.scene_selection_queue_sync_timer()
    rr.clear_inherited_rr_identity_before_save(None)
    check(root.get(rr.EXPORT_STABLE_ID_PROP) == "export-AppendRoot", "Append lost export identity on save")
    check(rr.is_object_manager_assembly_root(root), "Append lost group identity on save")
    check(child.parent == root and child.get(rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP) == root.get(rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP), "Append lost membership")

    # The import handler must also protect deliberate repeated imports. Identity
    # collision validation belongs to Export, not destructive save-time cleanup.
    if hasattr(bpy.app.handlers, "blend_import_post"):
        second = append_collection(path)
        second_root = next(obj for obj in second.objects if rr.is_object_manager_assembly_root(obj))
        rr.clear_inherited_rr_identity_before_save(None)
        check(second_root.get(rr.EXPORT_STABLE_ID_PROP) == "export-AppendRoot", "Repeated Append was mistaken for native duplication")

    # Real native duplication without the managed Shift+D macro is still caught.
    rr.reset_object_manager_duplicate_guard()
    select_only(child)
    bpy.ops.object.duplicate()
    duplicate = bpy.context.view_layer.objects.active
    check(duplicate != child and duplicate.get(rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP), "Native duplicate fixture failed")
    rr.clear_inherited_rr_identity_before_save(None)
    check(not rr.object_has_inherited_rr_identity(duplicate), "Native duplicate retained inherited RR identity")
    check(duplicate.parent is None, "Native member duplicate remained parented to the original group")
    check(rr.is_object_manager_assembly_root(root), "Native duplicate cleanup damaged original")

    # Unique identities made by scripts on Blender versions without import hooks
    # must likewise survive: a new UID is not enough evidence of duplication.
    unique, unique_child = make_group(rr, "UniqueScriptGroup")
    rr.clear_inherited_rr_identity_before_save(None)
    check(rr.is_object_manager_assembly_root(unique) and unique_child.parent == unique, "Unique new group was cleared")

    clear_scene(rr)
    root, child = make_group(rr, "ManagedVariant")
    rr.reset_object_manager_duplicate_guard()
    variant, copied = rr.duplicate_object_manager_group(bpy.context, root)
    rr.clear_inherited_rr_identity_before_save(None)
    check(rr.is_object_manager_assembly_root(variant), "Managed Duplicate Variant lost identity")
    check(variant.get(rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP) != root.get(rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP), "Managed variant reused group ID")
    check(any(obj.parent == variant for obj in copied if obj != variant), "Managed variant lost hierarchy")

    # A native member duplicate explicitly accepted as an authored collider is
    # now intentional; saving must not detach it from its selected owner.
    select_only(child)
    bpy.ops.object.duplicate()
    authored_collider = bpy.context.view_layer.objects.active
    root.select_set(True)
    bpy.context.view_layer.objects.active = root
    rr.associate_selected_collider(bpy.context)
    rr.clear_inherited_rr_identity_before_save(None)
    check(authored_collider.parent == root, "Save detached an explicitly associated native duplicate collider")
    check(authored_collider.get("rr_collider_target") == root.name, "Associated collider lost its owner")


def test_queue(rr):
    clear_scene(rr)
    settings = bpy.context.scene.rr_builder_export_settings
    first = make_object("QueueFirst")
    second = make_object("QueueSecond")
    selected = make_object("QueueSelected", mesh=True)
    for root in (first, second):
        settings.export_queue.add().object_name = root.name
    select_only(selected)
    rr.set_queue_active_index_without_preview_sync(settings, 0)
    with mock.patch.object(rr, "queue_root_from_scene_selection", wraps=rr.queue_root_from_scene_selection) as lookup:
        rr.sync_queue_active_index_to_scene_selection(bpy.context)
        for _ in range(5):
            rr.sync_queue_active_index_to_scene_selection(bpy.context)
        check(lookup.call_count == 1, "Unchanged selection repeated full membership lookup")
        settings.export_queue.add().object_name = make_object("QueueThird").name
        rr.sync_queue_active_index_to_scene_selection(bpy.context)
        check(lookup.call_count == 2, "Queue edit did not invalidate lookup")
        settings.export_queue[2].object_name = selected.name
        rr.sync_queue_active_index_to_scene_selection(bpy.context)
        check(settings.queue_active_index == 2, "Direct queued selection was missed after queue edit")

    settings.export_queue.remove(2)
    rr.set_queue_active_index_without_preview_sync(settings, 0)
    selected.parent = second
    rr.sync_queue_active_index_to_scene_selection(bpy.context)
    check(settings.queue_active_index == 1, "Parent change with unchanged selection was missed")
    selected.parent = None
    first[rr.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    first[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "queue-first"
    first[rr.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = first.name
    selected[rr.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = first.name
    rr.sync_queue_active_index_to_scene_selection(bpy.context)
    check(settings.queue_active_index == 0, "Custom-property membership edit was missed")

    rr.reset_object_manager_name_sync_state()
    first.name = "QueueFirstRenamed"
    rr.sync_object_manager_names()
    rr.set_queue_active_index_without_preview_sync(settings, 1)
    rr.sync_queue_active_index_to_scene_selection(bpy.context)
    check(settings.export_queue[0].object_name == first.name and settings.queue_active_index == 0, "Rename broke cached queue association")

    rr.clear_object_manager_props(selected)
    selected["rr_collider_target"] = second.name
    rr.sync_queue_active_index_to_scene_selection(bpy.context)
    check(settings.queue_active_index == 1, "Collider target edit did not invalidate lookup")
    del selected["rr_collider_target"]

    # Real Blender undo/redo restores object IDs as well as relationships.
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message="Queue membership baseline")
    selected.parent = bpy.data.objects["QueueFirstRenamed"]
    bpy.ops.ed.undo_push(message="Queue membership parent")
    rr.sync_queue_active_index_to_scene_selection(bpy.context)
    check(bpy.context.scene.rr_builder_export_settings.queue_active_index == 0, "Pre-undo parent association failed")
    bpy.ops.ed.undo()
    settings = bpy.context.scene.rr_builder_export_settings
    selected = bpy.data.objects["QueueSelected"]
    check(selected.parent is None, "Undo fixture did not restore membership")
    rr.sync_queue_active_index_to_scene_selection(bpy.context)
    bpy.ops.ed.redo()
    rr.sync_queue_active_index_to_scene_selection(bpy.context)
    check(bpy.context.scene.rr_builder_export_settings.queue_active_index == 0, "Redo left stale queue association")


def test_persistent_reference_migration(rr, directory):
    clear_scene(rr)
    scene = bpy.context.scene
    reference = make_object("LegacyReference", mesh=True)
    reference[rr.REFERENCE_MARK_PROP] = True
    scene.rr_builder_reference_layout.reference_object = reference
    settings = scene.rr_builder_export_settings
    settings.use_reference_layout = False
    settings.reference_layout_state_initialized = False
    path = os.path.join(directory, "legacy_reference.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    for _ in range(2):
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
        settings = bpy.context.scene.rr_builder_export_settings
        check(settings.reference_layout_state_initialized and settings.use_reference_layout, "Reference migration did not survive file loading")
        check(rr.migrate_reference_layout_usage_on_load in bpy.app.handlers.load_post, "Reference migration handler was removed on load")


def main():
    check(not hasattr(bpy.types.Scene, "rr_builder_export_settings"), "Use --factory-startup")
    rr = importlib.import_module(ADDON_NAME)
    rr.__time__ = os.path.getmtime(rr.__file__)
    rr = addon_utils.enable(ADDON_NAME, default_set=False, refresh_handled=True)
    try:
        with tempfile.TemporaryDirectory(prefix="rr_identity_queue_") as directory:
            test_identity(rr, directory)
            # Isolate queue lookup from the UI callback that intentionally
            # selects the newly active queue root and prepares its icon camera.
            with mock.patch.object(rr, "SYNCING_QUEUE_ACTIVE_INDEX", True):
                test_queue(rr)
            test_persistent_reference_migration(rr, directory)
    finally:
        addon_utils.disable(ADDON_NAME, default_set=False, refresh_handled=True)
    print(f"RR_IDENTITY_QUEUE_REGRESSIONS_PASS checks={CHECKS}")


if __name__ == "__main__":
    main()
