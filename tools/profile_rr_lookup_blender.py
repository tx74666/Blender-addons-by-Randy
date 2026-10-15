"""Compare RR lookup sources in one disposable background Blender process.

Example (the caller must reserve Blender/Unity resources and run serially):
  blender --background --factory-startup --python-exit-code 1 --python
  tools/profile_rr_lookup_blender.py -- --baseline-init <baseline_init.py>
  --output <report.json> [--source <saved.blend>] [--call-counts]

The synthetic fixture always runs. --source additionally measures the saved
file, never the GUI's unsaved state. Neither exporter is registered: these
queries need saved ID properties, not RNA settings, handlers or timers. The
baseline freezes __init__.py only; both packages use current canonical sibling
modules. No source is saved, no export is performed, and no Unity request is
created. Timings describe these synchronous queries, not GUI FPS or click delay.
"""

import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import functools
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import traceback


REPOSITORY = Path(__file__).resolve().parents[1]
PACKAGE = REPOSITORY / "addons" / "random_realm_builder_exporter"
DEFAULT_BASELINE = Path(
    "D:/Blender/Projects/Build/WIP/Validation/rr_performance_20261015/baseline_init.py"
)
WARMUP_ROUNDS = 2
MEASURED_ROUNDS = 5
ORDER = ("baseline", "candidate", "candidate", "baseline")
DEFAULT_SAVED_ROOTS = (
    "Hub_Elevator_A", "Hub_EntranceFrame_Rounded_A",
    "Hub_Floor_Circular_A", "Hub_Wall_Circular_A",
)
COUNTED_FUNCTIONS = (
    "object_manager_member_objects", "object_manager_selection_objects",
    "get_collision_meshes", "get_asset_meshes", "get_export_asset_meshes",
    "direct_object_manager_member_objects", "object_manager_direct_child_objects",
    "object_manager_assembly_root_for_object", "get_export_roots",
    "expand_related_export_roots", "live_export_identity_roots",
    "collect_export_identity_conflicts", "validate_export_identity",
    "validate_export_batch_identities",
)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def memory_snapshot():
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total_phys", ctypes.c_ulonglong), ("available_phys", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong), ("available_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                        ("available_extended", ctypes.c_ulonglong)]
        value = MemoryStatus()
        value.length = ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return {"total_bytes": value.total_phys, "available_bytes": value.available_phys,
                    "used_percent": value.load, "provider": "GlobalMemoryStatusEx"}
    else:
        try:
            import psutil
            value = psutil.virtual_memory()
            return {"total_bytes": value.total, "available_bytes": value.available,
                    "used_percent": value.percent, "provider": "psutil"}
        except ImportError:
            pass
    return {"total_bytes": None, "available_bytes": None, "used_percent": None,
            "provider": "unavailable"}


def memory_gate():
    snapshot = memory_snapshot()
    if snapshot["available_bytes"] is not None and snapshot["available_bytes"] < 200 * 1024 * 1024:
        raise RuntimeError("Less than 200 MiB available; no further benchmark operation started.")
    if snapshot["used_percent"] is not None and snapshot["used_percent"] >= 98:
        # This tool is already serial. Re-read pressure before doing more work.
        snapshot = memory_snapshot()
        if snapshot["available_bytes"] is not None and snapshot["available_bytes"] < 200 * 1024 * 1024:
            raise RuntimeError("Memory recheck fell below 200 MiB; benchmark stopped.")
        snapshot["near_98_percent"] = True
    return snapshot


def native_id(value):
    if value is None:
        return None
    library = getattr(value, "library", None)
    return [type(value).__name__, getattr(value, "name_full", value.name),
            getattr(library, "filepath", "") if library else ""]


def primitive(value):
    import bpy
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"nonfinite_float": repr(value)}
    if isinstance(value, bpy.types.ID):
        return {"native_id": native_id(value)}
    if hasattr(value, "keys"):
        return {str(key): primitive(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, set):
        return sorted((primitive(item) for item in value), key=repr)
    try:
        return [primitive(item) for item in value]
    except (AttributeError, TypeError):
        return {"type": type(value).__name__, "representation": str(value)}


def custom_properties(value):
    try:
        keys = value.keys()
    except (AttributeError, TypeError):
        # Some non-ID RNA containers (for example particular modifiers) do not
        # support custom properties. Top-level bpy.data IDs always do.
        return {}
    return {str(key): primitive(value[key]) for key in sorted(keys, key=str)}


def simple_rna(value):
    """Authored scalar/array/ID-pointer settings; omit readonly runtime counters."""
    import bpy
    result = {}
    for prop in value.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.is_readonly or prop.type == "COLLECTION":
            continue
        item = getattr(value, prop.identifier)
        if prop.type != "POINTER" or item is None or isinstance(item, bpy.types.ID):
            result[prop.identifier] = primitive(item)
    return result


def protected_signature():
    """Hash the documented authored fields, streaming geometry to bound RAM use.

    This is a data guard, not a claim of byte-equivalent entire Blender memory.
    It covers top-level bpy.data IDs' custom properties; scene/collection membership and
    selection; object transforms, hierarchy, visibility, modifier/constraint
    settings; mesh coordinates/topology/material slots/weights/UV/attributes/
    Shape Keys; and Curve/Font authored data. Evaluation caches are excluded.
    """
    import bpy
    digest = hashlib.sha256()
    records = 0

    def add(label, value):
        nonlocal records
        encoded = json.dumps([label, primitive(value)], sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "little"))
        digest.update(encoded)
        records += 1

    add("context", {"filepath": bpy.data.filepath, "dirty": bpy.data.is_dirty,
                    "mode": bpy.context.mode, "scene": native_id(bpy.context.scene),
                    "view_layer": bpy.context.view_layer.name,
                    "active": native_id(bpy.context.view_layer.objects.active),
                    "selection": [native_id(obj) for obj in bpy.context.selected_objects]})
    for prop in bpy.data.bl_rna.properties:
        if prop.type != "COLLECTION":
            continue
        for block in getattr(bpy.data, prop.identifier):
            if isinstance(block, bpy.types.ID):
                add("id", [prop.identifier, native_id(block), block.as_pointer(),
                           getattr(block, "session_uid", None), custom_properties(block)])
    for scene in bpy.data.scenes:
        add("scene", [native_id(scene), scene.frame_current, scene.frame_subframe,
                      list(scene.objects.keys()), list(scene.collection.children.keys())])
    for collection in bpy.data.collections:
        add("collection", [native_id(collection), list(collection.objects.keys()),
                           list(collection.children.keys()), collection.hide_viewport,
                           collection.hide_render, collection.hide_select])
    view_objects = set(bpy.context.view_layer.objects)
    for obj in bpy.data.objects:
        view_state = [obj.hide_get(), obj.select_get()] if obj in view_objects else None
        add("object", [native_id(obj), obj.type, native_id(obj.data), native_id(obj.parent),
                       obj.parent_type, obj.parent_bone, obj.matrix_basis, obj.matrix_parent_inverse,
                       obj.matrix_world, obj.hide_viewport, obj.hide_render, obj.hide_select,
                       view_state, list(obj.users_collection),
                       [group.name for group in obj.vertex_groups],
                       [[item.name, item.type, simple_rna(item), custom_properties(item)]
                        for item in obj.modifiers],
                       [[item.name, item.type, simple_rna(item), custom_properties(item)]
                        for item in obj.constraints],
                       [[slot.link, native_id(slot.material)] for slot in obj.material_slots]])
    for mesh in bpy.data.meshes:
        add("mesh", [native_id(mesh), list(mesh.materials), mesh.use_fake_user])
        for vertex in mesh.vertices:
            add("vertex", [vertex.co, [[group.group, group.weight] for group in vertex.groups]])
        for edge in mesh.edges:
            add("edge", [list(edge.vertices), edge.use_seam, edge.use_edge_sharp])
        for polygon in mesh.polygons:
            add("polygon", [list(polygon.vertices), polygon.material_index, polygon.use_smooth])
        for loop in mesh.loops:
            add("loop", [loop.vertex_index, loop.edge_index])
        for layer in mesh.uv_layers:
            add("uv_layer", [layer.name, layer.active_render])
            for item in layer.data:
                add("uv", item.uv)
        for attribute in mesh.attributes:
            add("attribute", [attribute.name, attribute.domain, attribute.data_type, len(attribute.data)])
            for item in attribute.data:
                field = next((name for name in ("value", "vector", "color") if hasattr(item, name)), None)
                if field is None:
                    raise RuntimeError(f"Data guard cannot read attribute {attribute.name!r} ({attribute.data_type}).")
                add("attribute_value", getattr(item, field))
        if mesh.shape_keys is not None:
            for key in mesh.shape_keys.key_blocks:
                add("shape_key", [key.name, key.value, key.mute, key.slider_min, key.slider_max,
                                  getattr(key.relative_key, "name", None), key.vertex_group])
                for item in key.data:
                    add("shape_key_point", item.co)
    for curve in bpy.data.curves:
        add("curve", [native_id(curve), simple_rna(curve), list(curve.materials)])
        if hasattr(curve, "body_format"):
            add("font_format", [simple_rna(item) for item in curve.body_format])
        if hasattr(curve, "text_boxes"):
            add("font_text_boxes", [simple_rna(item) for item in curve.text_boxes])
        for spline in curve.splines:
            add("spline", simple_rna(spline))
            for point in spline.points:
                add("curve_point", simple_rna(point))
            for point in spline.bezier_points:
                add("bezier_point", simple_rna(point))
    return {"sha256": digest.hexdigest(), "records": records,
            "scope": protected_signature.__doc__.strip()}


@contextmanager
def readonly_scope(module, *, cache):
    previous_repair = module.OBJECT_MANAGER_REPAIR_ALLOWED
    previous_cache = module._EXPORT_IDENTITY_LOOKUP_CACHE
    module.OBJECT_MANAGER_REPAIR_ALLOWED = False
    module._EXPORT_IDENTITY_LOOKUP_CACHE = {} if cache else None
    try:
        yield
    finally:
        module._EXPORT_IDENTITY_LOOKUP_CACHE = previous_cache
        module.OBJECT_MANAGER_REPAIR_ALLOWED = previous_repair


def object_labels(objects):
    return [native_id(obj) for obj in objects]


def draw_like_lookup(module, groups):
    with readonly_scope(module, cache=True):
        return [{"group": native_id(root),
                 "members": object_labels(module.object_manager_member_objects(root)),
                 "selection": object_labels(module.object_manager_selection_objects(root)),
                 "collisions": object_labels(module.get_collision_meshes(root))} for root in groups]


def discover(module):
    with readonly_scope(module, cache=False):
        return module.live_export_identity_roots()


def conflicts(module, roots):
    with readonly_scope(module, cache=False):
        return [{"root": native_id(root), "conflicts": module.collect_export_identity_conflicts(root)}
                for root in roots]


def validate_batch(module, roots):
    with readonly_scope(module, cache=False):
        try:
            result = module.validate_export_batch_identities(roots)
        except RuntimeError as exception:
            return {"status": "rejected", "error": str(exception)}
        return {"status": "accepted", "roots": object_labels(result)}


@contextmanager
def entry_counts(module):
    counts, originals = {}, {}
    try:
        for name in COUNTED_FUNCTIONS:
            original = getattr(module, name)
            originals[name] = original

            def instrument(function, label):
                @functools.wraps(function)
                def wrapper(*args, **kwargs):
                    counts[label] = counts.get(label, 0) + 1
                    return function(*args, **kwargs)
                return wrapper

            setattr(module, name, instrument(original, name))
        yield counts
    finally:
        for name, original in originals.items():
            setattr(module, name, original)


def compare_operation(name, operation, modules, report, call_counts):
    record = {"name": name, "warmups": [], "samples": [], "ordered_output_equal": False}
    report["operations"].append(record)
    expected = None

    def invoke(version, phase, round_index, position):
        nonlocal expected
        pressure = memory_gate()
        start = time.perf_counter_ns()
        result = operation(modules[version])
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        if expected is None:
            expected = result
        elif result != expected:
            record["mismatch"] = {"version": version, "phase": phase,
                                  "round": round_index, "position": position,
                                  "expected": expected, "actual": result}
            raise AssertionError(f"Ordered output differs: {report['kind']} / {name} / {version}.")
        sample = {"version": version, "round": round_index, "position": position,
                  "milliseconds": elapsed_ms, "memory_before": pressure}
        record[phase].append(sample)

    for round_index in range(WARMUP_ROUNDS):
        for position, version in enumerate(("baseline", "candidate")):
            invoke(version, "warmups", round_index, position)
    for round_index in range(MEASURED_ROUNDS):
        for position, version in enumerate(ORDER):
            invoke(version, "samples", round_index, position)
    record["ordered_output_equal"] = True
    record["ordered_output"] = expected
    record["median_ms"] = {
        version: statistics.median(item["milliseconds"] for item in record["samples"]
                                   if item["version"] == version) for version in modules
    }
    if call_counts:
        record["entry_call_counts"] = {}
        for version, module in modules.items():
            with entry_counts(module) as counts:
                result = operation(module)
            if result != expected:
                raise AssertionError(f"Instrumented ordered output differs: {name} / {version}.")
            record["entry_call_counts"][version] = dict(sorted(counts.items()))
        record["call_count_scope"] = "Separate untimed invocation; entry calls include memo hits."


def fixture(module):
    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)

    def triangle(name, parent=None, collider=False):
        data = bpy.data.meshes.new(name + "Mesh")
        data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        data.update()
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
        obj.parent = parent
        if parent is not None:
            obj[module.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = parent[module.OBJECT_MANAGER_ASSEMBLY_ID_PROP]
            obj[module.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = parent.name
        if collider:
            obj["rr_builder_collision_helper"] = True
            obj["rr_collider_target"] = parent.name
        return obj

    groups = []
    for index in range(8):
        root = bpy.data.objects.new(f"Lookup_Group_{index + 1:02d}", None)
        bpy.context.scene.collection.objects.link(root)
        root[module.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
        root[module.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = f"lookup_assembly_{index + 1:02d}"
        root[module.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = root.name
        root[module.EXPORT_STABLE_ID_PROP] = f"lookup_asset_{index + 1:02d}"
        if groups:
            root.parent = groups[-1]
            root[module.OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = groups[-1].name
            root[module.OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = groups[-1][module.OBJECT_MANAGER_ASSEMBLY_ID_PROP]
        groups.append(root)
        triangle(root.name + "_Part", root)
        triangle(root.name + "_Collider", root, collider=True)
    for index in range(80):
        triangle(f"Lookup_Unrelated_{index + 1:03d}")
    bpy.context.view_layer.update()
    return groups


def benchmark_dataset(kind, modules, report, groups, call_counts, root_names=()):
    import bpy
    section = {"kind": kind, "operations": [], "object_count": len(bpy.data.objects),
               "group_count": len(groups), "source_filepath": bpy.data.filepath,
               "data_protection_before": protected_signature()}
    report["datasets"].append(section)
    try:
        roots_a, roots_b = discover(modules["baseline"]), discover(modules["candidate"])
        if object_labels(roots_a) != object_labels(roots_b):
            section["discovery_mismatch"] = {"baseline": object_labels(roots_a),
                                             "candidate": object_labels(roots_b)}
            raise AssertionError(f"Preflight ordered roots differ: {kind}.")
        section["root_count"] = len(roots_a)
        if not roots_a:
            raise RuntimeError(f"No live export roots found in {kind}; no meaningful lookup timing possible.")
        # Fixture batch includes every nested group, not only the top discovery root.
        roots = groups if kind == "fixture" else roots_a
        if kind == "saved_source":
            requested = tuple(root_names) or DEFAULT_SAVED_ROOTS
            roots_by_name = {root.name: root for root in roots_a}
            section["requested_targets"] = list(requested)
            section["missing_targets"] = [name for name in requested if name not in roots_by_name]
            if root_names and any(name not in roots_by_name for name in requested):
                raise ValueError("Every --root must name a discovered saved-source export root.")
            selected = [roots_by_name[name] for name in requested if name in roots_by_name]
            roots = selected or roots_a[:4]
            section["used_first_four_fallback"] = not bool(selected)
            section["target_scope"] = (
                "Requested/default building roots, or first four discovered roots when none match. "
                "Global discovery and conflict peers still cover all live roots."
            )
        section["batch_roots"] = object_labels(roots)
        if kind == "fixture":
            for version, module in modules.items():
                outcome = validate_batch(module, roots)
                if outcome["status"] != "accepted":
                    section["unexpected_fixture_rejection"] = {"version": version, "outcome": outcome}
                    raise AssertionError(f"Synthetic fixture identities unexpectedly rejected by {version}.")
        operations = [
            ("live_export_identity_roots", lambda module: object_labels(discover(module))),
            ("conflicts_each_root", lambda module: conflicts(module, roots)),
            ("validate_export_batch_identities", lambda module: validate_batch(module, roots)),
        ]
        if groups:
            operations.insert(0, ("draw_like_group_lookup", lambda module: draw_like_lookup(module, groups)))
        else:
            section["skipped_operations"] = {"draw_like_group_lookup": "No authored assembly groups in this dataset."}
        for name, operation in operations:
            compare_operation(name, operation, modules, section, call_counts)
    finally:
        section["data_protection_after"] = protected_signature()
        section["data_protection_equal"] = (
            section["data_protection_before"] == section["data_protection_after"]
        )
        if not section["data_protection_equal"]:
            raise AssertionError(f"Authored data/context protection signature changed: {kind}.")


def validated_output(path, protected_paths, unity_project):
    path = path.expanduser().resolve()
    if path.suffix.casefold() != ".json" or path in protected_paths:
        raise ValueError("--output must be a separate .json report file.")
    for ancestor in (path, *path.parents):
        if ancestor.name.casefold() == "assets" or ancestor == unity_project:
            raise ValueError("Benchmark reports cannot be written inside a Unity project or Assets directory.")
        if (ancestor / "Assets").is_dir() and (ancestor / "ProjectSettings").is_dir():
            raise ValueError("Benchmark report resolves inside a Unity project.")
    return path


def main(argv=None):
    import bpy
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-init", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, help="Additionally read this saved .blend; never saves it.")
    parser.add_argument("--root", action="append", default=[],
                        help="Saved-source conflict/batch target name; repeat to select several")
    parser.add_argument("--call-counts", action="store_true")
    args = parser.parse_args(argv if argv is not None else (
        sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    ))
    if args.root and not args.source:
        parser.error("--root requires --source")
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("Use a disposable --background --factory-startup Blender process.")
    baseline_path = args.baseline_init.expanduser().resolve()
    if not baseline_path.is_file():
        raise ValueError(f"Baseline __init__.py not found: {baseline_path}")
    source = args.source.expanduser().resolve() if args.source else None
    if source is not None and (not source.is_file() or source.suffix.casefold() != ".blend"):
        raise ValueError("--source must be an existing saved .blend file.")
    initial_memory = memory_gate()
    sys.path.insert(0, str(REPOSITORY / "addons"))
    candidate = importlib.import_module("random_realm_builder_exporter")
    if Path(candidate.__file__).resolve() != (PACKAGE / "__init__.py").resolve():
        raise RuntimeError("Candidate was not imported from the canonical repository package.")
    spec = importlib.util.spec_from_file_location(
        "rr_lookup_baseline", baseline_path, submodule_search_locations=[str(PACKAGE)]
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not create the baseline alias package loader.")
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    modules = {"baseline": baseline, "candidate": candidate}
    output = validated_output(args.output, {baseline_path, source, (PACKAGE / "__init__.py").resolve(),
                                            Path(__file__).resolve()},
                              Path(candidate.UNITY_PROJECT_ROOT).resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError(f"Refusing to overwrite an existing benchmark report: {output}")
    report = {
        "schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running", "performance_conclusion_allowed": False,
        "blender": {"version": bpy.app.version_string, "binary": bpy.app.binary_path,
                    "background": bpy.app.background},
        "tool_sha256": file_sha256(__file__), "memory_before": initial_memory,
        "candidate": {"version": list(candidate.bl_info["version"]),
                      "init_path": str(PACKAGE / "__init__.py"),
                      "init_sha256": file_sha256(PACKAGE / "__init__.py"), "registered": False},
        "baseline": {"version": list(baseline.bl_info["version"]), "init_path": str(baseline_path),
                     "init_sha256": file_sha256(baseline_path), "registered": False},
        "canonical_python_sha256": {str(path.relative_to(PACKAGE)): file_sha256(path)
                                     for path in sorted(PACKAGE.rglob("*.py"))},
        "baseline_dependency_scope": "Frozen baseline __init__.py; current canonical sibling modules.",
        "design": {"warmup_rounds_per_version": WARMUP_ROUNDS, "measured_rounds": MEASURED_ROUNDS,
                   "order_per_measured_round": list(ORDER), "same_process_same_loaded_data": True,
                   "draw_scope": "Both versions: repair=False, fresh cache={}; restored on exit.",
                   "identity_scope": "Both versions: repair=False, cache=None; source owns nested scopes.",
                   "timing_scope": "Synchronous queries plus ordered result labels; excludes signatures, memory gates and optional call counts."},
        "source": str(source) if source else None,
        "source_sha256_before": file_sha256(source) if source else None,
        "datasets": [],
        "limits": [
            "Shared machine load, sequence, OS caches and dependency-graph state may affect timing.",
            "Fixture timing is not a saved scene measurement; saved source is not unsaved GUI state.",
            "These queries do not measure panel drawing, export/render, GUI FPS or click latency.",
            "A batch ordered_output status of rejected measures the identical rejection path, not a successful export batch.",
            "Data guard covers its enumerated authored fields, not every native runtime allocation.",
            "No source save, add-on registration, Unity request or Unity asset write is performed.",
        ],
    }
    try:
        groups = fixture(candidate)
        benchmark_dataset("fixture", modules, report, groups, args.call_counts)
        if source is not None:
            memory_gate()
            bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
            if Path(bpy.data.filepath).resolve() != source:
                raise RuntimeError("Blender did not open the requested saved source.")
            groups = [obj for obj in bpy.data.objects if candidate.is_object_manager_assembly_root(obj)]
            benchmark_dataset("saved_source", modules, report, groups, args.call_counts, args.root)
        report["source_sha256_after"] = file_sha256(source) if source else None
        if report["source_sha256_after"] != report["source_sha256_before"]:
            raise AssertionError("Saved source file hash changed.")
        for section in report["datasets"]:
            for operation in section["operations"]:
                original = operation["median_ms"]["baseline"]
                current = operation["median_ms"]["candidate"]
                operation["median_reduction_percent"] = (100 * (original - current) / original
                                                          if original > 0 else None)
        report["performance_conclusion_allowed"] = True
        report["status"] = "complete"
    except BaseException:
        report["status"] = "failed"
        report["error"] = traceback.format_exc()
        raise
    finally:
        report["source_sha256_after"] = file_sha256(source) if source else None
        source_changed = report["source_sha256_after"] != report["source_sha256_before"]
        if source_changed:
            report["status"] = "failed"
            report["performance_conclusion_allowed"] = False
            report["source_file_changed"] = True
        report["memory_after"] = memory_snapshot()
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        temporary = output.with_name(output.name + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                             encoding="utf-8")
        os.replace(temporary, output)
        if source_changed:
            raise AssertionError("Saved source file hash changed; benchmark conclusions rejected.")
    print(f"RR_LOOKUP_PROFILE_COMPLETE {output}")


if __name__ == "__main__":
    main()
