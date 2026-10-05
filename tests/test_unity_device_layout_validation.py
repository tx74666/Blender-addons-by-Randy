"""Pure protocol/coordinate contracts; run with python -B, without Blender."""

import copy
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import zlib


_test_directory = Path(__file__).resolve().parent
_addons_directory = _test_directory.parent / "addons"
if _addons_directory.exists():
    if not _addons_directory.is_dir():
        raise NotADirectoryError(f"Expected an addon directory: {_addons_directory}")
    RUNTIME_ADDON_DIRECTORY = (_addons_directory / "random_realm_builder_exporter").resolve()
    RUNTIME_MODULE_PATH = (RUNTIME_ADDON_DIRECTORY / "rr_unity_device_layout.py").resolve()
    if not RUNTIME_MODULE_PATH.is_file():
        raise FileNotFoundError(
            f"Formal addon exists but its Unity layout module is missing: {RUNTIME_MODULE_PATH}. "
            "Refusing to fall back to a staging module."
        )
else:
    RUNTIME_ADDON_DIRECTORY = None
    RUNTIME_MODULE_PATH = (_test_directory / "rr_unity_device_layout.py").resolve()
    if not RUNTIME_MODULE_PATH.is_file():
        raise FileNotFoundError(f"Staging Unity layout module is missing: {RUNTIME_MODULE_PATH}")

# Always execute this exact file; a cached module from another checkout must not
# silently replace the runtime under test.
_runtime_module_name = "_rr_unity_device_layout_validation_runtime_" + uuid.uuid4().hex
_runtime_spec = importlib.util.spec_from_file_location(_runtime_module_name, RUNTIME_MODULE_PATH)
if _runtime_spec is None or _runtime_spec.loader is None:
    raise ImportError(f"Cannot load Unity layout module: {RUNTIME_MODULE_PATH}")
layout = importlib.util.module_from_spec(_runtime_spec)
sys.modules[_runtime_module_name] = layout
try:
    _runtime_spec.loader.exec_module(layout)
except BaseException:
    sys.modules.pop(_runtime_module_name, None)
    raise


IDENTITY = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]
def _png_chunk(kind, content):
    return (struct.pack(">I", len(content)) + kind + content +
            struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff))


# A complete 1x1 RGBA PNG with CRCs generated from its actual chunk contents.
PNG = (b"\x89PNG\r\n\x1a\n" +
       _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) +
       _png_chunk(b"IDAT", zlib.compress(b"\x00\x40\x80\xc0\xff")) +
       _png_chunk(b"IEND", b""))


def package():
    """One ordinary device, mesh part and material, with every v1 field."""
    return {
        "schema": "random-realm.unity-device-layout",
        "version": 1,
        "coordinateSpace": "UNITY",
        "units": "meters",
        "snapshotId": "ed0615cf-64b4-4139-b559-e63605b5899d",
        "scenePath": "Assets/Scenes/Main/Adventure.unity",
        "reference": {
            "id": "reference-001",
            "name": "Hub Core",
            "sourceStableId": "rr_asset_reference_001",
            "sourceBlend": "D:/Blender/Projects/Builder.blend",
            "authoringFrameInRoot": IDENTITY.copy(),
            "worldMatrix": IDENTITY.copy(),
        },
        "devices": [{
            "id": "device-001",
            "name": "Display",
            "globalObjectId": "GlobalObjectId_V1-2-0123456789abcdef0123456789abcdef-123-0",
            "prefabGuid": "fedcba9876543210fedcba9876543210",
            "relativeMatrix": IDENTITY.copy(),
            "parts": [{
                "name": "Body",
                "meshId": "mesh-001",
                "relativeMatrix": IDENTITY.copy(),
                "materialIds": ["material-001"],
            }],
            "arcId": "arc-001",
            "slotId": "slot-001",
        }],
        "meshes": [{
            "id": "mesh-001",
            "vertices": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            "uv": [0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
            "submeshes": [{"triangles": [0, 1, 2]}],
        }],
        "materials": [{
            "id": "material-001",
            "name": "Metal",
            "color": [0.3, 0.4, 0.5, 1.0],
            "emission": [0.0, 0.0, 0.0],
            "texturePath": "",
        }],
    }


def append_copy(items, identity):
    result = copy.deepcopy(items[0])
    result["id"] = identity
    items.append(result)
    return result


class PackageValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rr-layout-contract-")
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name) / "snapshot"
        self.base.mkdir()
        self.preview = self.base / "previews" / "screen.png"
        self.preview.parent.mkdir()
        self.preview.write_bytes(PNG)
        self.payload = package()

    def validate(self, payload=None):
        return layout.validate_package(self.payload if payload is None else payload, str(self.base))

    def invalid(self, payload=None):
        with self.assertRaises(layout.LayoutValidationError):
            self.validate(payload)

    def add_screen(self, path="previews/screen.png"):
        self.payload["devices"][0]["screenPreview"] = {
            "path": path, "relativeMatrix": IDENTITY.copy(), "width": 1.0, "height": 1.0,
        }
        return self.payload["devices"][0]["screenPreview"]

    def test_default_screen_preview_holders_normalize_to_absent(self):
        holders = [{}]
        for path in (None, ""):
            for matrix in (None, []):
                holders.append({"path": path, "relativeMatrix": matrix, "width": 0, "height": 0})
        for holder in holders:
            with self.subTest(holder=holder):
                self.payload["devices"][0]["screenPreview"] = copy.deepcopy(holder)
                self.assertIsNone(self.validate()["devices"][0]["screenPreview"])

    def test_partial_or_invalid_screen_preview_holders_are_rejected(self):
        holders = [
            {"path": "", "relativeMatrix": [], "width": 0, "height": 1},
            {"path": None, "relativeMatrix": None, "width": 1, "height": 0},
            {"path": "", "relativeMatrix": IDENTITY.copy(), "width": 0, "height": 0},
            {"path": "previews/screen.png", "relativeMatrix": [], "width": 0, "height": 0},
            {"path": "", "relativeMatrix": [], "width": None, "height": 0},
            {"path": "", "relativeMatrix": [], "width": False, "height": 0},
            {"path": "", "relativeMatrix": [], "width": 0, "height": float("nan")},
        ]
        for holder in holders:
            with self.subTest(holder=holder):
                self.payload["devices"][0]["screenPreview"] = copy.deepcopy(holder)
                self.invalid()

    def test_error_is_a_value_error(self):
        self.assertTrue(issubclass(layout.LayoutValidationError, ValueError))

    def test_minimal_package_normalizes_and_preserves_optional_extensions(self):
        self.payload["capturedInPlay"] = True
        self.payload["devices"][0]["parentGlobalObjectId"] = "parent-object"
        result = self.validate()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["reference"]["id"], "reference-001")
        self.assertEqual(result["devices"][0]["relativeMatrix"], IDENTITY)
        self.assertTrue(result["capturedInPlay"])
        self.assertEqual(result["devices"][0]["parentGlobalObjectId"], "parent-object")

    def test_devices_without_static_geometry_remain_valid(self):
        self.payload["devices"][0]["parts"] = []
        self.payload["meshes"] = []
        self.payload["materials"] = []
        result = self.validate()
        self.assertEqual(result["devices"][0]["name"], "Display")
        self.assertEqual(result["devices"][0]["parts"], [])

    def test_screen_can_be_absent_or_null(self):
        self.validate()
        self.payload["devices"][0]["screenPreview"] = None
        self.validate()

    def test_existing_relative_png_and_empty_or_existing_texture_are_valid(self):
        self.add_screen()
        self.validate()
        self.payload["materials"][0]["texturePath"] = "previews/screen.png"
        self.validate()

    def test_required_reference_fields_cannot_be_missing(self):
        for field in ("id", "name", "sourceStableId", "sourceBlend", "authoringFrameInRoot", "worldMatrix"):
            with self.subTest(field=field):
                candidate = package()
                del candidate["reference"][field]
                self.invalid(candidate)

    def test_protocol_discriminators_and_snapshot_uuid_are_checked(self):
        for field, value in (
            ("schema", "another.protocol"), ("version", 2),
            ("coordinateSpace", "BLENDER"), ("units", "centimeters"),
            ("snapshotId", "runtime-instance-123"), ("snapshotId", ""),
        ):
            with self.subTest(field=field, value=value):
                candidate = package()
                candidate[field] = value
                self.invalid(candidate)

    def test_duplicate_device_mesh_and_material_ids_are_rejected(self):
        for collection in ("devices", "meshes", "materials"):
            with self.subTest(collection=collection):
                candidate = package()
                candidate[collection].append(copy.deepcopy(candidate[collection][0]))
                self.invalid(candidate)

    def test_empty_object_ids_are_rejected(self):
        for collection in ("devices", "meshes", "materials"):
            with self.subTest(collection=collection):
                candidate = package()
                candidate[collection][0]["id"] = ""
                self.invalid(candidate)
        self.payload["reference"]["id"] = ""
        self.invalid()

    def test_all_transform_locations_reject_nonfinite_affine_and_singular_matrices(self):
        self.add_screen()
        original = copy.deepcopy(self.payload)
        for location in ("authoringFrameInRoot", "worldMatrix", "device", "part", "screen"):
            for fault in ("nan", "infinity", "perspective", "last_element", "singular", "near_singular", "length"):
                with self.subTest(location=location, fault=fault):
                    candidate = copy.deepcopy(original)
                    if location in ("authoringFrameInRoot", "worldMatrix"):
                        matrix = candidate["reference"][location]
                    elif location == "device":
                        matrix = candidate["devices"][0]["relativeMatrix"]
                    elif location == "part":
                        matrix = candidate["devices"][0]["parts"][0]["relativeMatrix"]
                    else:
                        matrix = candidate["devices"][0]["screenPreview"]["relativeMatrix"]
                    if fault == "nan":
                        matrix[3] = math.nan
                    elif fault == "infinity":
                        matrix[0] = math.inf
                    elif fault == "perspective":
                        matrix[12] = 0.1
                    elif fault == "last_element":
                        matrix[15] = 0.0
                    elif fault == "singular":
                        matrix[0] = 0.0
                    elif fault == "near_singular":
                        matrix[0] = 1e-13
                    else:
                        matrix.pop()
                    self.invalid(candidate)

    def test_invertible_mirrored_nonuniform_transform_is_valid(self):
        self.payload["devices"][0]["relativeMatrix"][0] = -2.0
        self.payload["devices"][0]["relativeMatrix"][5] = 3.0
        self.payload["devices"][0]["relativeMatrix"][10] = 4.0
        self.validate()

    def test_vertex_uv_and_triangle_counts_are_consistent(self):
        for field, value in (
            ("vertices", [0.0, 1.0]),
            ("vertices", [0.0, 0.0, math.nan, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            ("uv", [0.0, 0.0]),
            ("uv", [0.0, 0.0, 1.0, 0.0, math.inf, 1.0]),
            ("submeshes", [{"triangles": [0, 1]}]),
            ("submeshes", [{"triangles": [0, 1, 3]}]),
            ("submeshes", [{"triangles": [0, -1, 2]}]),
            ("submeshes", [{"triangles": [0, 1.0, 2]}]),
            ("submeshes", [{"triangles": [0, True, 2]}]),
        ):
            with self.subTest(field=field, value=value):
                candidate = package()
                candidate["meshes"][0][field] = value
                self.invalid(candidate)

    def test_part_mesh_and_material_references_are_checked(self):
        for field, value in (("meshId", "missing-mesh"), ("materialIds", ["missing-material"])):
            with self.subTest(field=field, value=value):
                candidate = package()
                candidate["devices"][0]["parts"][0][field] = value
                self.invalid(candidate)

    def test_material_slots_may_be_empty_short_long_or_repeat_known_materials(self):
        self.payload["meshes"][0]["submeshes"].append({"triangles": [0, 2, 1]})
        for ids in ([], ["material-001"], ["material-001"] * 2, ["material-001"] * 3):
            with self.subTest(material_ids=ids):
                candidate = copy.deepcopy(self.payload)
                candidate["devices"][0]["parts"][0]["materialIds"] = ids
                result = self.validate(candidate)
                self.assertEqual(result["devices"][0]["parts"][0]["materialIds"], ids)

    def test_material_color_and_emission_have_finite_expected_components(self):
        for field, value in (("color", [0.0, 0.0, 0.0]), ("color", [0.0, 0.0, math.nan, 1.0]),
                             ("emission", [0.0, 0.0]), ("emission", [0.0, math.inf, 0.0])):
            with self.subTest(field=field):
                candidate = package()
                candidate["materials"][0][field] = value
                self.invalid(candidate)

    def test_screen_dimensions_are_finite_and_positive(self):
        screen = self.add_screen()
        for field in ("width", "height"):
            for value in (0.0, -1.0, math.nan, math.inf):
                with self.subTest(field=field, value=value):
                    previous = screen[field]
                    screen[field] = value
                    self.invalid()
                    screen[field] = previous

    def test_screen_and_texture_paths_reject_absolute_traversal_and_missing_files(self):
        self.add_screen()
        paths = (
            "../outside.png", "previews/../../outside.png", "previews\\..\\..\\outside.png",
            str(self.preview.resolve()), "/outside.png", "C:\\outside.png", "\\\\server\\share\\screen.png",
            "previews/missing.png", "previews/screen.png\x00", "",
        )
        for target in ("screen", "texture"):
            for path in paths:
                if target == "texture" and path == "":
                    continue  # Empty texturePath explicitly means an untextured material.
                with self.subTest(target=target, path=path):
                    candidate = copy.deepcopy(self.payload)
                    if target == "screen":
                        candidate["devices"][0]["screenPreview"]["path"] = path
                    else:
                        candidate["materials"][0]["texturePath"] = path
                    self.invalid(candidate)

    def test_png_path_cannot_point_to_a_directory(self):
        directory = self.base / "folder.png"
        directory.mkdir()
        self.add_screen("folder.png")
        self.invalid()

    def test_symlink_cannot_escape_snapshot_directory(self):
        outside = Path(self.directory.name) / "outside.png"
        outside.write_bytes(PNG)
        link = self.base / "linked.png"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest("Filesystem cannot create a test symlink: " + str(error))
        self.add_screen("linked.png")
        self.invalid()

    def test_collection_and_geometry_limits_without_large_allocations(self):
        cases = []
        candidate = package()
        append_copy(candidate["devices"], "device-002")
        cases.append(("MAX_DEVICES", 1, candidate))
        candidate = package()
        append_copy(candidate["meshes"], "mesh-002")
        cases.append(("MAX_MESHES", 1, candidate))
        cases.append(("MAX_VERTICES_PER_MESH", 2, package()))
        candidate = package()
        append_copy(candidate["meshes"], "mesh-002")
        cases.append(("MAX_VERTICES_TOTAL", 5, candidate))
        candidate = package()
        candidate["devices"][0]["parts"].append(copy.deepcopy(candidate["devices"][0]["parts"][0]))
        cases.append(("MAX_PARTS_PER_DEVICE", 1, candidate))
        candidate = package()
        append_copy(candidate["devices"], "device-002")
        cases.append(("MAX_PARTS_TOTAL", 1, candidate))
        candidate = package()
        append_copy(candidate["materials"], "material-002")
        cases.append(("MAX_MATERIALS", 1, candidate))
        candidate = package()
        candidate["meshes"][0]["submeshes"][0]["triangles"] = [0, 1, 2] * 3
        cases.append(("MAX_TRIANGLES_TOTAL", 2, candidate))
        for constant, limit, payload in cases:
            with self.subTest(constant=constant), mock.patch.object(layout, constant, limit):
                self.invalid(payload)

    def test_limits_are_inclusive_at_the_boundary(self):
        for constant, limit in (("MAX_DEVICES", 1), ("MAX_MESHES", 1), ("MAX_MATERIALS", 1),
                                ("MAX_PARTS_PER_DEVICE", 1), ("MAX_PARTS_TOTAL", 1),
                                ("MAX_VERTICES_PER_MESH", 3), ("MAX_VERTICES_TOTAL", 3),
                                ("MAX_TRIANGLES_TOTAL", 1)):
            with self.subTest(constant=constant), mock.patch.object(layout, constant, limit):
                self.validate()

    def test_read_package_validates_json_and_relative_preview_base(self):
        self.add_screen()
        path = self.base / "test.rr-layout.json"
        path.write_text(json.dumps(self.payload), encoding="utf-8")
        result = layout.read_package(str(path))
        self.assertEqual(result["snapshotId"], self.payload["snapshotId"])
        self.assertEqual(result["devices"][0]["id"], "device-001")

    def test_read_package_rejects_missing_malformed_and_oversized_files(self):
        missing = self.base / "missing.rr-layout.json"
        with self.assertRaises(layout.LayoutValidationError):
            layout.read_package(str(missing))
        path = self.base / "test.rr-layout.json"
        for text in ("{broken json", "[]", '{"schema":"other"}'):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(layout.LayoutValidationError):
                    layout.read_package(str(path))
        path.write_text(json.dumps(self.payload), encoding="utf-8")
        with mock.patch.object(layout, "MAX_FILE_BYTES", 16):
            with self.assertRaises(layout.LayoutValidationError):
                layout.read_package(str(path))


class CoordinateConversionTests(unittest.TestCase):
    def assert_sequence_close(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for index, (a, b) in enumerate(zip(actual, expected)):
            self.assertAlmostEqual(a, b, places=8, msg="component " + str(index))

    def test_identity_and_translation_use_inverse_existing_reference_basis(self):
        self.assert_sequence_close(layout.unity_to_blender_matrix(IDENTITY), IDENTITY)
        translated = IDENTITY.copy()
        translated[3], translated[7], translated[11] = 1.0, 2.0, 3.0
        expected = IDENTITY.copy()
        expected[3], expected[7], expected[11] = -1.0, -3.0, 2.0
        self.assert_sequence_close(layout.unity_to_blender_matrix(translated), expected)

    def test_rotation_nonuniform_scale_and_translation_are_converted_together(self):
        # Unity +90deg Y, scale (2,3,4), translation (1,2,3).
        # Its Blender counterpart is -90deg Z, scale (2,4,3), translation (-1,-3,2).
        unity = [0.0, 0.0, 4.0, 1.0, 0.0, 3.0, 0.0, 2.0,
                 -2.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 1.0]
        expected = [0.0, 4.0, 0.0, -1.0, -2.0, 0.0, 0.0, -3.0,
                    0.0, 0.0, 3.0, 2.0, 0.0, 0.0, 0.0, 1.0]
        self.assert_sequence_close(layout.unity_to_blender_matrix(unity), expected)

    def test_units_scale_positions_without_changing_local_scale(self):
        unity = [2.0, 0.0, 0.0, 1.0, 0.0, 3.0, 0.0, 2.0,
                 0.0, 0.0, 4.0, 3.0, 0.0, 0.0, 0.0, 1.0]
        expected = [2.0, 0.0, 0.0, -100.0, 0.0, 4.0, 0.0, -300.0,
                    0.0, 0.0, 3.0, 200.0, 0.0, 0.0, 0.0, 1.0]
        self.assert_sequence_close(layout.unity_to_blender_matrix(unity, meters_per_unit=0.01), expected)
        self.assert_sequence_close(layout.unity_to_blender_vertices([1.0, 2.0, 3.0], meters_per_unit=0.01),
                                   [-100.0, -300.0, 200.0])

    def test_vertices_convert_all_axes_and_preserve_input(self):
        vertices = [1.0, 2.0, 3.0, -4.0, 5.0, -6.0]
        before = vertices.copy()
        self.assert_sequence_close(layout.unity_to_blender_vertices(vertices), [-1.0, -3.0, 2.0, 4.0, 6.0, 5.0])
        self.assertEqual(vertices, before)

    def test_invalid_units_are_rejected_by_both_converters(self):
        for units in (0.0, -1.0, math.nan, math.inf):
            for function, data in ((layout.unity_to_blender_matrix, IDENTITY),
                                   (layout.unity_to_blender_vertices, [0.0, 0.0, 0.0])):
                with self.subTest(function=function.__name__, units=units):
                    with self.assertRaises(ValueError):
                        function(data, meters_per_unit=units)

    def test_triangle_winding_reverses_each_triangle_without_mutating_input(self):
        triangles = [0, 1, 2, 2, 3, 0]
        self.assertEqual(layout.unity_triangles_to_blender(triangles), [0, 2, 1, 2, 0, 3])
        self.assertEqual(triangles, [0, 1, 2, 2, 3, 0])
        self.assertEqual(layout.unity_triangles_to_blender([]), [])

    def test_reference_marker_uses_object_get_without_blender(self):
        class PropertyObject:
            def __init__(self, value):
                self.value = value
                self.calls = []

            def get(self, key, default=None):
                self.calls.append(key)
                return self.value

        self.assertFalse(layout.is_unity_layout_reference(None))
        self.assertFalse(layout.is_unity_layout_reference({}))
        for value in (False, True):
            with self.subTest(value=value):
                obj = PropertyObject(value)
                self.assertEqual(layout.is_unity_layout_reference(obj), value)
                self.assertTrue(obj.calls)


if __name__ == "__main__":
    unittest.main()


