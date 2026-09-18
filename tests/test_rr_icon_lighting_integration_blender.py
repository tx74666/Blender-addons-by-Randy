"""Real icon rendering and exporter/UI integration for saved HDRI lighting.

Run in its own Blender --background --factory-startup process. All rendered
files and outline sources live in TemporaryDirectory; no Bridge/Unity writes.
"""

import contextlib
import os
from pathlib import Path
import sys
import tempfile
import warnings
from types import SimpleNamespace
from unittest import mock

import bpy


ADDONS = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDONS))
import random_realm_builder_exporter as rr
from random_realm_builder_exporter import rr_icon_lighting as lighting

warnings.filterwarnings("ignore", category=DeprecationWarning)

CHECKS = 0
BRIGHTNESS_KEYS = (
    "rr_icon_light_brightness", "rr_icon_key_light_ratio",
    "rr_icon_fill_light_ratio", "rr_icon_back_light_ratio",
)


def check(condition, message):
    global CHECKS
    if not condition:
        raise AssertionError(message)
    CHECKS += 1


def plain(value):
    if isinstance(value, bpy.types.ID):
        return type(value).__name__, value.name
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "items"):
        return {key: plain(item) for key, item in value.items()}
    try:
        return tuple(plain(item) for item in value)
    except TypeError:
        return repr(value)


def node_state(tree):
    if tree is None:
        return None
    return (
        tuple((node.name, node.bl_idname, tuple((socket.name, plain(socket.default_value)) for socket in node.inputs if hasattr(socket, "default_value"))) for node in tree.nodes),
        tuple((link.from_node.name, link.from_socket.name, link.to_node.name, link.to_socket.name) for link in tree.links),
    )


def scene_snapshot(scene):
    objects = {}
    for obj in bpy.data.objects:
        item = {
            "type": obj.type,
            "matrix": plain(obj.matrix_world),
            "hide": (obj.hide_render, obj.hide_viewport, obj.hide_get(), obj.hide_select),
            "properties": plain(obj),
            "data_name": obj.data.name if obj.data is not None else None,
        }
        # plain(ID) intentionally returns an identity; explicitly capture IDProperties.
        item["properties"] = {key: plain(value) for key, value in obj.items()}
        if obj.type == "MESH":
            item["vertices"] = tuple(plain(vertex.co) for vertex in obj.data.vertices)
            item["polygons"] = tuple(tuple(face.vertices) for face in obj.data.polygons)
            item["materials"] = tuple(material.name if material is not None else None for material in obj.data.materials)
            item["uvs"] = tuple((layer.name, tuple(plain(loop.uv) for loop in layer.data)) for layer in obj.data.uv_layers)
        elif obj.type == "LIGHT":
            item["light"] = tuple((key, plain(getattr(obj.data, key, None))) for key in ("type", "energy", "color", "use_custom_distance", "cutoff_distance", "spot_size", "spot_blend", "shadow_soft_size"))
        objects[obj.name] = item
    render = scene.render
    return {
        "objects": objects,
        "camera": scene.camera.name if scene.camera else None,
        "world": scene.world.name if scene.world else None,
        "world_data": tuple((world.name, node_state(world.node_tree), plain(world.color)) for world in bpy.data.worlds),
        "camera_data": tuple(camera.name for camera in bpy.data.cameras),
        "image_data": tuple((image.name, image.filepath, image.alpha_mode, image.colorspace_settings.name) for image in bpy.data.images if image.type not in {"RENDER_RESULT", "COMPOSITING"}),
        "materials": tuple((material.name, node_state(material.node_tree), plain(material.diffuse_color)) for material in bpy.data.materials),
        "render": tuple((key, plain(getattr(render, key))) for key in ("engine", "filepath", "resolution_x", "resolution_y", "resolution_percentage", "film_transparent")),
        "format": (render.image_settings.file_format, render.image_settings.color_mode, render.image_settings.color_depth),
        "view": (scene.view_settings.view_transform, scene.view_settings.look, scene.view_settings.exposure, scene.view_settings.gamma),
    }


def select(obj):
    for item in bpy.context.selected_objects:
        item.select_set(False)
    bpy.context.view_layer.objects.active = obj
    if obj is not None:
        obj.select_set(True)


def check_snapshot(scene, before, message):
    after = scene_snapshot(scene)
    differences = {key: (before[key], after[key]) for key in before if before[key] != after[key]}
    if differences:
        print("LIGHTING_STATE_DIFFERENCES", differences, flush=True)
    check(not differences, message)


def cube(name, location=(0, 0, 0), scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_cube_add(size=2, location=location)
    result = bpy.context.object
    result.name = name
    result.scale = scale
    return result


def group(name, children, kind="ASSEMBLY"):
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    root[rr.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    root[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "lighting_" + name
    root[rr.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = name
    root[rr.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = kind
    root[rr.OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP] = True
    for child in children:
        child.parent = root
        if rr.is_object_manager_assembly_root(child):
            child[rr.OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = name
            child[rr.OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = root[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP]
        else:
            child[rr.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = name
            child[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = root[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP]
    return root


class RenderFailure(RuntimeError):
    pass


@contextlib.contextmanager
def intercept_render(callback):
    # bpy.ops creates submodule proxy objects dynamically. Replace the top-level
    # proxy temporarily so code's next bpy.ops.render.render lookup uses this hook.
    original_ops = bpy.ops

    class OpsProxy:
        render = SimpleNamespace(render=callback)

        def __getattr__(self, name):
            return getattr(original_ops, name)

    with mock.patch.object(bpy, "ops", OpsProxy()):
        yield


@contextlib.contextmanager
def reject_three_point_writes():
    with contextlib.ExitStack() as stack:
        for name in ("ensure_icon_preview_lights", "save_icon_light_transforms_to_object", "save_icon_light_transform_to_object"):
            stack.enter_context(mock.patch.object(rr, name, side_effect=AssertionError("HDRI called legacy lighting: " + name)))
        yield


def root_resolution_tests(settings, profile):
    leaf = cube("LightingAssemblyLeaf", location=(20, 0, 0))
    assembly = group("LightingAssembly", [leaf])
    select(leaf)
    check(rr.current_icon_lighting_root(bpy.context, settings) == assembly, "Assembly member did not resolve its lighting owner")

    variant_leaf = cube("LightingVariantLeaf", location=(30, 0, 0))
    variant_assembly = group("LightingVariantAssembly", [variant_leaf])
    source = cube("LightingVariantSource", location=(32, 0, 0))
    variants = group("LightingVariants", [variant_assembly, source], "VARIANTS")
    variants[rr.OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP] = source.name
    for selected, expected in ((variants, source), (variant_assembly, variant_assembly), (variant_leaf, variant_assembly), (source, source)):
        select(selected)
        check(rr.current_icon_lighting_root(bpy.context, settings) == expected, "Variants selection did not resolve the selected member's prospective icon source")

    # Capturing another member commits its lighting and the group's icon source
    # together, so subsequent Export cannot silently render the older member.
    lighting.write_profile(source, {"version": 1, "mode": "LEGACY"})
    variant_assembly[rr.EXPORT_STABLE_ID_PROP] = "lighting_selected_variant_identity"
    old_source_profile = source[lighting.PROFILE_KEY]
    select(variant_leaf)
    check(rr.set_current_icon_lighting(bpy.context, profile) == variant_assembly, "Capture did not resolve a member inside a Variant Assembly")
    check(rr.shared_builder_icon_root(variant_leaf) == variant_assembly, "Capture did not update the shared export icon source")
    check(variants[rr.OBJECT_MANAGER_VARIANT_ICON_SOURCE_STABLE_ID_PROP] == "lighting_selected_variant_identity", "Capture did not retain the selected source's existing stable ID")
    check(lighting.read_profile(variant_assembly, bpy.context.scene)["mode"] == "HDRI", "Old source LEGACY overrode the newly selected source profile")
    check(source[lighting.PROFILE_KEY] == old_source_profile, "Capture overwrote the older Variant source's own lighting")

    low = cube("InnerWall_Lighting_Low_200x10x190", location=(40, 0, 0), scale=(1, 0.05, 0.95))
    full = cube("InnerWall_Lighting_Full_200x10x200", location=(43, 0, 0), scale=(1, 0.05, 1))
    select(full)
    check(rr.current_icon_lighting_root(bpy.context, settings) == low, "InnerWall full member did not resolve its shared Low source")

    settings.export_queue.clear()
    item = settings.export_queue.add()
    item.object_name = assembly.name
    select(source)
    check(rr.current_icon_lighting_root(bpy.context, settings) == source, "Stale queue overrode active lighting source")
    select(None)
    check(rr.current_icon_lighting_root(bpy.context, settings) == assembly, "Lighting owner did not fall back to queue with no selection")
    settings.export_queue.clear()


def profile_transaction_tests(profile):
    class Owner(dict):
        is_editable = True
        name = "SelectedVariant"
        fail_key = None

        def __setitem__(self, key, value):
            if key == self.fail_key:
                self.fail_key = None
                raise RenderFailure("Intentional profile transaction write failure")
            return super().__setitem__(key, value)

    old_profile = lighting.serialize_profile({"version": 1, "mode": "LEGACY"})
    owner = Owner({lighting.PROFILE_KEY: old_profile, rr.EXPORT_STABLE_ID_PROP: "selected_variant_stable"})
    scene = Owner({lighting.PROFILE_KEY: old_profile})
    group_owner = Owner({rr.OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP: "OldSource", rr.OBJECT_MANAGER_VARIANT_ICON_SOURCE_STABLE_ID_PROP: "old_stable"})
    before = tuple(dict(item) for item in (owner, scene, group_owner))
    group_owner.fail_key = rr.OBJECT_MANAGER_VARIANT_ICON_SOURCE_STABLE_ID_PROP
    with mock.patch.object(rr, "current_icon_lighting_root", return_value=owner), mock.patch.object(rr, "object_manager_variant_group_root", return_value=group_owner):
        try:
            rr.set_current_icon_lighting(SimpleNamespace(scene=scene), profile, scene_default=True)
        except RenderFailure:
            pass
        else:
            raise AssertionError("Injected final group write failure was swallowed")
    check(tuple(dict(item) for item in (owner, scene, group_owner)) == before, "Capture failed to roll back root, scene, and group source as one transaction")


def main():
    check(not hasattr(bpy.types.Scene, "rr_builder_export_settings"), "Use --factory-startup in an isolated process")
    rr.register()
    try:
        scene = bpy.context.scene
        settings = scene.rr_builder_export_settings
        settings.export_queue.clear()
        settings.icon_outline_enabled = False
        root = bpy.data.objects.get("Cube")
        root.name = "LightingIntegrationCube"
        root.location = (0, 0, 0)
        select(root)
        material = bpy.data.materials.new("LightingIntegrationMaterial")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        principled.inputs["Base Color"].default_value = (0.7, 0.2, 0.08, 1)
        principled.inputs["Roughness"].default_value = 0.4
        root.data.materials.clear()
        root.data.materials.append(material)
        blocker = cube("UnrelatedRenderBlocker", location=(0, -3, 0))
        blocker.hide_render = False
        hidden = cube("OriginallyHiddenGeometry", location=(0, 3, 0))
        hidden.hide_render = True
        select(root)

        studio = next((light for light in bpy.context.preferences.studio_lights if light.name == "forest.exr" and light.type == "WORLD"), None)
        hdri_path = studio.path if studio is not None else ""
        check(os.path.isfile(hdri_path), "Bundled forest.exr is unavailable for the real render test")
        profile = {"version": 1, "mode": "HDRI", "studio_name": "forest.exr", "hdri_path": hdri_path, "rotation_z": 0.45, "intensity": 0.8, "world_space": True, "sun_threshold": 8.0}
        lighting.write_profile(scene, {"version": 1, "mode": "LEGACY"})
        lighting.write_profile(root, profile)
        for number, key in enumerate(BRIGHTNESS_KEYS):
            root[key] = 9.0 + number
        for number, spec in enumerate(rr.ICON_PREVIEW_LIGHT_SPECS):
            data = bpy.data.lights.new(spec["name"] + "_Data", "SPOT")
            data.energy = 71.0 + number
            light = bpy.data.objects.new(spec["name"], data)
            scene.collection.objects.link(light)
            light.location = (number + 1, -4, 6)
            light.hide_render = number == 1
            light["rr_icon_preview_light"] = True
            light["rr_icon_preview_light_target"] = root.name
            light["rr_icon_preview_focus"] = [0.2, 0.3, 0.4]
            root[rr.icon_light_transform_property(spec)] = [float(value) for row in light.matrix_world for value in row]
            root[rr.icon_light_state_property(spec, "cutoff_distance")] = 37.0 + number

        scene.render.engine = "BLENDER_WORKBENCH"
        scene.render.resolution_x = 111
        scene.render.resolution_y = 87
        scene.render.resolution_percentage = 37
        scene.render.film_transparent = False
        scene.render.image_settings.file_format = "JPEG"
        scene.render.image_settings.color_mode = "BW"
        scene.render.image_settings.color_depth = "8"
        original_world = scene.world
        original_camera = scene.camera
        real_render = bpy.ops.render.render

        def inspect_render(expected_strength):
            check(scene.world is not original_world, "HDRI render did not install a temporary World")
            check(scene.render.engine in {"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"}, "HDRI render did not select EEVEE")
            check(scene.camera is not original_camera, "HDRI render did not use its temporary camera")
            check(all(obj.hide_render for obj in scene.objects if obj.type == "LIGHT"), "HDRI render retained a scene/preview light")
            check(blocker.hide_render and hidden.hide_render and not root.hide_render, "HDRI render isolation is incorrect")
            check(scene.render.film_transparent and scene.render.resolution_percentage == 100, "HDRI output framing was not configured")
            background = next(node for node in scene.world.node_tree.nodes if node.bl_idname == "ShaderNodeBackground")
            check(abs(background.inputs["Strength"].default_value - expected_strength) < 1e-6, "Root/scene profile precedence was not used by render_icon")

        with tempfile.TemporaryDirectory(prefix="rr_icon_lighting_integration_") as directory:
            scene.render.filepath = os.path.join(directory, "untouched.jpg")
            icon_path = os.path.join(directory, "hdri.png")
            with mock.patch.object(rr, "UNITY_BUILDER_ICON_SOURCE_CACHE", os.path.join(directory, "outline_sources")):
                bpy.context.view_layer.update()
                before = scene_snapshot(scene)

                def actual_render(**kwargs):
                    inspect_render(profile["intensity"])
                    return real_render(**kwargs)

                with reject_three_point_writes(), intercept_render(actual_render):
                    rr.render_icon(root, icon_path, 64, settings)
                check_snapshot(scene, before, "Successful HDRI render changed source state or leaked temporary data")
                check(os.path.isfile(icon_path) and os.path.getsize(icon_path) > 100, "Real HDRI render produced no PNG")
                image = bpy.data.images.load(icon_path, check_existing=False)
                try:
                    check(tuple(image.size) == (64, 64), "Real render did not honor requested resolution")
                    alpha = list(image.pixels)[3::4]
                    check(any(value > 0.5 for value in alpha) and any(value < 0.1 for value in alpha), "Real icon lacks visible geometry or transparent background")
                finally:
                    bpy.data.images.remove(image)

                # A scene default must work without changing any saved legacy ratios.
                del root[lighting.PROFILE_KEY]
                scene_profile = dict(profile, intensity=1.35)
                lighting.write_profile(scene, scene_profile)
                before = scene_snapshot(scene)

                def failing_render(**kwargs):
                    inspect_render(scene_profile["intensity"])
                    raise RenderFailure("Intentional render failure")

                try:
                    with reject_three_point_writes(), intercept_render(failing_render):
                        rr.render_icon(root, os.path.join(directory, "failed.png"), 64, settings)
                except RenderFailure:
                    pass
                else:
                    raise AssertionError("Injected rendering failure was swallowed")
                check_snapshot(scene, before, "Failed HDRI render changed source state or leaked temporary data")
                check(not os.path.exists(os.path.join(directory, "failed.png")), "Failed render left an output icon")

                # A cancelled render must not treat an already existing PNG as a
                # fresh result, cache it again, or apply another outline to it.
                previous_png = Path(icon_path).read_bytes()

                def cancelled_render(**kwargs):
                    inspect_render(scene_profile["intensity"])
                    return {"CANCELLED"}

                with reject_three_point_writes(), intercept_render(cancelled_render), mock.patch.object(rr, "remember_icon_outline_source") as remember, mock.patch.object(rr, "apply_icon_outline_to_png") as outline:
                    try:
                        rr.render_icon(root, icon_path, 64, settings)
                    except RuntimeError as exc:
                        check("cancelled" in str(exc).lower(), "Cancelled render did not provide a clear failure")
                    else:
                        raise AssertionError("Cancelled render was treated as a successful export")
                    check(remember.call_count == 0 and outline.call_count == 0, "Cancelled render processed the previous PNG as a new result")
                check(Path(icon_path).read_bytes() == previous_png, "Cancelled render changed the previous PNG")
                check_snapshot(scene, before, "Cancelled HDRI render changed source state or leaked temporary data")

                old_ratios = {key: root[key] for key in BRIGHTNESS_KEYS}
                settings.icon_zoom = 1.17
                rr.save_icon_framing_to_object(root, settings)
                check({key: root[key] for key in BRIGHTNESS_KEYS} == old_ratios, "Scene HDRI framing save overwrote old three-point brightness")
                check(abs(root["rr_icon_zoom"] - 1.17) < 1e-5, "HDRI framing save stopped saving framing")
                lighting.write_profile(root, profile)
                rr.save_icon_framing_to_object(root, settings)
                check({key: root[key] for key in BRIGHTNESS_KEYS} == old_ratios, "Root HDRI framing save overwrote old three-point brightness")

                # Asset LEGACY overrides scene HDRI and retains the original path.
                lighting.write_profile(root, {"version": 1, "mode": "LEGACY"})
                rr.save_icon_framing_to_object(root, settings)
                expected_ratios = (settings.icon_light_brightness, settings.icon_key_light_ratio, settings.icon_fill_light_ratio, settings.icon_back_light_ratio)
                check(tuple(root[key] for key in BRIGHTNESS_KEYS) == expected_ratios, "Explicit LEGACY did not save its brightness fields")
                before = scene_snapshot(scene)
                with mock.patch.object(rr, "ensure_icon_preview_lights", side_effect=RenderFailure("Legacy branch reached")) as ensure:
                    try:
                        rr.render_icon(root, os.path.join(directory, "legacy.png"), 64, settings)
                    except RenderFailure:
                        pass
                    else:
                        raise AssertionError("Explicit LEGACY did not execute the legacy render branch")
                    check(ensure.call_count == 1, "Explicit LEGACY did not override scene HDRI")
                check_snapshot(scene, before, "LEGACY branch setup failure leaked temporary render state")

                # Operators own profile writes; their shared helper capture is tested separately.
                select(root)
                with mock.patch.object(lighting, "capture_profile", return_value=profile) as capture:
                    check(bpy.ops.rr_builder.use_viewport_icon_lighting() == {"FINISHED"}, "Viewport-lighting operator failed")
                    check(capture.call_count == 1, "Viewport-lighting operator did not capture current viewport")
                check(lighting.read_profile(root)["mode"] == "HDRI" and lighting.read_profile(scene)["mode"] == "HDRI", "Viewport-lighting operator did not save root and scene default")
                saved_scene_profile = scene[lighting.PROFILE_KEY]
                # Keep current, unsaved camera framing while loading only the
                # archived three-light ratios when changing the lighting mode.
                framing_values = {
                    "icon_zoom": 1.43, "icon_offset_x": 0.13, "icon_offset_y": -0.17,
                    "icon_view_yaw": 0.61, "icon_view_pitch": 0.23,
                }
                previous_sync = rr.SYNCING_QUEUE_SETTINGS
                rr.SYNCING_QUEUE_SETTINGS = True
                try:
                    for key, value in framing_values.items():
                        setattr(settings, key, value)
                    settings.icon_light_brightness = 0.27
                finally:
                    rr.SYNCING_QUEUE_SETTINGS = previous_sync
                before_framing = tuple(getattr(settings, key) for key in framing_values)
                check(bpy.ops.rr_builder.use_three_point_icon_lighting() == {"FINISHED"}, "Three-point operator failed")
                check(lighting.read_profile(root)["mode"] == "LEGACY", "Three-point operator did not set asset LEGACY")
                check(scene[lighting.PROFILE_KEY] == saved_scene_profile, "Three-point operator overwrote the scene HDRI default")
                check(tuple(getattr(settings, key) for key in framing_values) == before_framing, "Switching to three lights reset the current camera framing")
                check(tuple(getattr(settings, key[3:]) for key in BRIGHTNESS_KEYS) == tuple(root[key] for key in BRIGHTNESS_KEYS), "Switching to three lights did not restore its saved brightness ratios")

        root_resolution_tests(settings, profile)
        profile_transaction_tests(profile)
    finally:
        rr.unregister()
    print(f"RR_ICON_LIGHTING_INTEGRATION_PASS checks={CHECKS}")


if __name__ == "__main__":
    main()
