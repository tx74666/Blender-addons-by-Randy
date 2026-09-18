import importlib
import os
import sys
import tempfile

import bpy
from mathutils import Vector


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
BLENDER_ROOT = os.path.dirname(TEST_DIR)
ADDONS_DIR = os.path.join(BLENDER_ROOT, "addons")
ADDON_MODULE_NAME = "random_realm_builder_exporter"
ADDON_INIT_PATH = os.path.realpath(
    os.path.join(ADDONS_DIR, ADDON_MODULE_NAME, "__init__.py")
)
EXPECTED_ALIAS = "柔性中心"
EXPECTED_LOCATION = Vector((1.25, -2.5, 3.75))


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def assert_vector_close(actual, expected, message, tolerance=1.0e-6):
    delta = Vector(actual) - Vector(expected)
    if delta.length > tolerance:
        raise AssertionError(
            f"{message}: expected {tuple(expected)!r}, got {tuple(actual)!r}"
        )


def clear_addon_modules():
    for name in list(sys.modules):
        if name == ADDON_MODULE_NAME or name.startswith(f"{ADDON_MODULE_NAME}."):
            sys.modules.pop(name, None)


def import_canonical_addon():
    if ADDONS_DIR not in sys.path:
        sys.path.insert(0, ADDONS_DIR)
    importlib.invalidate_caches()
    module = importlib.import_module(ADDON_MODULE_NAME)
    actual_path = os.path.realpath(module.__file__)
    if os.path.normcase(actual_path) != os.path.normcase(ADDON_INIT_PATH):
        raise AssertionError(
            "Lifecycle test imported a non-canonical RR Helper: "
            f"expected {ADDON_INIT_PATH!r}, got {actual_path!r}"
        )
    return module


def bookmark_slot(module):
    settings = bpy.context.scene.rr_builder_export_settings
    slot = module.point_bookmark_slot(settings, "G2", "P3")
    if slot is None:
        raise AssertionError("G2/P3 point bookmark slot is unavailable")
    return slot


def assert_bookmark(module, stage):
    slot = bookmark_slot(module)
    assert_equal(slot.is_set, True, f"{stage} stored state")
    assert_equal(slot.alias, EXPECTED_ALIAS, f"{stage} alias")
    assert_vector_close(slot.location, EXPECTED_LOCATION, f"{stage} location")
    assert_equal(slot.space, "WORLD", f"{stage} coordinate space")
    assert_equal(slot.source, "CURSOR", f"{stage} source")


def main():
    if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
        raise AssertionError(
            "RR Helper was already registered despite --factory-startup; "
            "the lifecycle test requires an isolated process"
        )

    descriptor, blend_path = tempfile.mkstemp(
        prefix="rr_point_bookmark_lifecycle_",
        suffix=".blend",
    )
    os.close(descriptor)
    os.remove(blend_path)
    module = None
    registered = False

    try:
        object_names = set(bpy.data.objects.keys())
        clear_addon_modules()
        module = import_canonical_addon()
        module.register()
        registered = True
        assert_equal(
            set(bpy.data.objects.keys()),
            object_names,
            "Registering RR Helper must not create objects",
        )

        slot = bookmark_slot(module)
        slot.alias = EXPECTED_ALIAS
        slot.location = tuple(EXPECTED_LOCATION)
        slot.space = "WORLD"
        slot.source = "CURSOR"
        slot.is_set = True
        assert_bookmark(module, "Before save")

        save_result = bpy.ops.wm.save_as_mainfile(filepath=blend_path, check_existing=False)
        assert_equal(save_result, {"FINISHED"}, "Saving lifecycle fixture")
        assert_equal(
            set(bpy.data.objects.keys()),
            object_names,
            "Saving the bookmark must not create objects",
        )

        open_result = bpy.ops.wm.open_mainfile(filepath=blend_path, load_ui=False)
        assert_equal(open_result, {"FINISHED"}, "Reopening lifecycle fixture")
        assert_bookmark(module, "After reopen")
        assert_equal(
            set(bpy.data.objects.keys()),
            object_names,
            "Reopening the bookmark fixture must not create objects",
        )

        module.unregister()
        registered = False
        clear_addon_modules()
        module = import_canonical_addon()
        module.register()
        registered = True

        assert_bookmark(module, "After simulated refresh")
        assert_equal(
            set(bpy.data.objects.keys()),
            object_names,
            "Refreshing RR Helper must not create objects",
        )
    finally:
        if registered and module is not None:
            module.unregister()
        clear_addon_modules()
        if os.path.exists(blend_path):
            os.remove(blend_path)

    if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
        raise AssertionError("RR Helper registration leaked after lifecycle cleanup")
    if os.path.exists(blend_path):
        raise AssertionError(f"Lifecycle fixture was not removed: {blend_path}")
    print("RR_POINT_BOOKMARK_LIFECYCLE_PASS")


if __name__ == "__main__":
    main()
