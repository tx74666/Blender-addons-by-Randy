"""Existing Selected previews follow asset ownership, independent of the queue."""

import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import bpy


ADDONS = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDONS))
import random_realm_builder_exporter as rr


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aW1sAAAAASUVORK5CYII=")
CHECKS = 0


def check(condition, message):
    global CHECKS
    if not condition:
        raise AssertionError(message)
    CHECKS += 1


def picture(path, modified=100):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    os.utime(path, (modified, modified))
    return str(path)


def mesh(name):
    data = bpy.data.meshes.new(name + "_Mesh")
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 0, 1)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def group(name, members, kind="ASSEMBLY"):
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    root[rr.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    root[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "preview_" + name
    root[rr.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = name
    root[rr.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = kind
    root[rr.OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP] = True
    for member in members:
        member.parent = root
        if rr.is_object_manager_assembly_root(member):
            member[rr.OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = name
            member[rr.OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = root[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP]
        else:
            member[rr.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = name
            member[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = root[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP]
    return root


def select(obj):
    for other in bpy.context.selected_objects:
        other.select_set(False)
    bpy.context.view_layer.objects.active = obj
    if obj is not None:
        obj.select_set(True)


def resolved(settings):
    return rr.current_preview_display_path(bpy.context, settings)


def state():
    scene = bpy.context.scene
    return {
        "objects": [(obj.name, tuple(tuple(row) for row in obj.matrix_world), dict(obj.items())) for obj in bpy.data.objects],
        "camera": scene.camera,
        "render": (scene.render.engine, scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage, scene.render.filepath),
        "framing": (scene.rr_builder_export_settings.icon_zoom, scene.rr_builder_export_settings.icon_view_yaw, scene.rr_builder_export_settings.icon_light_brightness),
    }


def check_preview_cache_lifecycle(base):
    rr.clear_preview_collections()
    current_path = picture(base / "cache" / "icon.png")
    other_path = picture(base / "cache" / "icon.png.other.png")
    collection = rr.get_preview_collection()
    rr.get_preview_icon_id(current_path)
    rr.get_preview_icon_id(other_path)
    # Background Blender allocates preview entries but leaves GPU icon IDs at 0.
    check(len(collection) == 2, "Preview cache did not load both images")
    initial_entries = dict(collection.items())
    other_key = next(key for key in collection if key.startswith(other_path + "|"))
    rr.get_preview_icon_id(current_path)
    check(dict(collection.items()) == initial_entries, "Unchanged preview should reuse its cached entry")
    for modified in range(101, 151):
        os.utime(current_path, (modified, modified))
        rr.get_preview_icon_id(current_path)
        check(len(collection) == 2, "Repeated Capture retained obsolete thumbnail versions")
    check(collection.get(other_key) == initial_entries[other_key], "Updating one image evicted another image")
    previous_keys = set(collection.keys())
    os.utime(current_path, (200, 200))
    with mock.patch.object(type(collection), "load", side_effect=RuntimeError("injected preview load failure")):
        check(rr.get_preview_icon_id(current_path) == 0, "Failed preview load must preserve existing failure behavior")
    check(set(collection.keys()) == previous_keys, "Failed replacement discarded the previous valid cache")
    rr.get_preview_icon_id(current_path)
    check(len(collection) == 2, "Recovery retained obsolete thumbnail versions")
    check(set(collection.keys()) != previous_keys, "Preview did not recover after a transient load failure")
    Path(current_path).unlink()
    check(rr.get_preview_icon_id(current_path) == 0, "Missing image should not display a stale thumbnail")
    check(collection.get(other_key) == initial_entries[other_key], "Missing image lookup affected another image")
    rr.clear_preview_collections()


def main():
    rr.register()
    try:
        settings = bpy.context.scene.rr_builder_export_settings
        settings.export_queue.clear()
        left, right = mesh("LobbyDoor_L"), mesh("LobbyDoor_R")
        door = group("LobbyDoor_400x10x400", [left, right])
        unrelated = mesh("Unrelated")
        missing = mesh("NoSavedPicture")
        variant_a, variant_b = mesh("Surface_A"), mesh("Surface_B")
        variants = group("Surface_Variants", [variant_a, variant_b], "VARIANTS")
        variants[rr.OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP] = variant_b.name

        with tempfile.TemporaryDirectory(prefix="rr_preview_selection_") as directory:
            base = Path(directory)
            check_preview_cache_lifecycle(base)
            generated = base / "Unity" / "Assets" / "Generated"
            bridge = base / "Bridge"
            output = base / "Output"
            index = base / "reference_index.json"
            settings.output_root = str(output)
            with mock.patch.multiple(rr, UNITY_PROJECT_ROOT=str(base / "Unity"), UNITY_GENERATED_BUILD_ART_ROOT=str(generated), UNITY_TEMP_OUTPUT_ROOT=str(bridge), UNITY_BUILDER_REFERENCE_INDEX=str(index)):
                generated_door = picture(generated / door.name / "icon.png")
                for selected in (left, right, door):
                    select(selected)
                    before = state()
                    check(resolved(settings) == generated_door, "Generated Assembly icon must follow its root and both members with no queue")
                    check(state() == before, "Preview lookup changed objects, rendering, or framing")
                check(not output.exists(), "Read-only preview lookup created output folders")

                # An unrelated queue item and the old global last-loaded image
                # must not override the asset actually selected in the scene.
                unrelated_icon = picture(output / unrelated.name / "icon.png", 150)
                item = settings.export_queue.add()
                item.object_name = unrelated.name
                item.preview_path = unrelated_icon
                settings.preview_image_path = unrelated_icon
                select(left)
                check(resolved(settings) == generated_door, "Stale active queue overrode Selected")
                check(rr.refresh_selected_preview_image(settings, bpy.context), "Explicit preview refresh failed")
                check(settings.preview_image_path == generated_door, "Explicit preview refresh loaded the unrelated queue image")
                select(missing)
                check(resolved(settings) == "", "An unrelated global image was displayed for an asset without an icon")
                select(None)
                check(resolved(settings) == unrelated_icon, "Queue fallback should work when nothing is selected")
                settings.export_queue.clear()

                # Preview and Export destinations coexist; the newer picture wins.
                select(left)
                preview = picture(output / "_icon_previews" / (door.name + "_icon_preview.png"), 300)
                exported = picture(output / door.name / "icon.png", 200)
                check(resolved(settings) == preview, "An old export hid a freshly rendered preview")
                os.utime(exported, (400, 400))
                check(resolved(settings) == exported, "A new export did not replace an older preview")

                # A Variants group has no own package: use its source/member icons.
                icon_a = picture(generated / variant_a.name / "icon.png", 100)
                icon_b = picture(generated / variant_b.name / "icon.png", 200)
                select(variants)
                check(resolved(settings) == icon_b, "Variants container did not resolve its stored icon source")
                os.utime(icon_a, (300, 300))
                select(variant_a)
                check(resolved(settings) == icon_a, "Selected variant did not resolve its own recent icon")
                Path(icon_a).unlink()
                check(resolved(settings) == icon_b, "Selected variant did not fall back to its shared source icon")
                Path(icon_b).unlink()
                select(variants)
                check(resolved(settings) == "", "Missing variant images returned another asset's picture")

                # A dashboard asset may use a nonstandard icon path and category.
                indexed = picture(base / "Unity" / "Assets" / "Custom" / "props.png", 100)
                index.write_text(json.dumps({"entries": [{"id": missing.name, "category": "Props", "iconPath": "Assets/Custom/props.png"}]}), encoding="utf-8")
                select(missing)
                check(resolved(settings) == indexed, "Exact index matching incorrectly excluded Props")
                indexed_new = picture(base / "Unity" / "Assets" / "Custom" / "material.png", 200)
                index.write_text(json.dumps({"entries": [{"id": missing.name, "category": "Material", "iconPath": "Assets/Custom/material.png"}]}), encoding="utf-8")
                check(resolved(settings) == indexed_new, "Reference index cache did not notice a file update")
                index.write_text("{malformed", encoding="utf-8")
                check(resolved(settings) == "", "Malformed index should not reuse an old unrelated result")
                index.write_text('{"entries": null}', encoding="utf-8")
                check(resolved(settings) == "", "Invalid index entries should not crash the preview panel")

                rr.OBJECT_MANAGER_REPAIR_ALLOWED = True
                with mock.patch.object(rr, "existing_preview_path", side_effect=RuntimeError("injected")):
                    try:
                        resolved(settings)
                    except RuntimeError:
                        pass
                check(rr.OBJECT_MANAGER_REPAIR_ALLOWED, "Read-only lookup did not restore repair flag after failure")
    finally:
        rr.unregister()
    print(f"RR_PREVIEW_SELECTION_PASS checks={CHECKS}")


if __name__ == "__main__":
    main()
