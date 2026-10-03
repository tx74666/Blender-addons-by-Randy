"""Profile saved RR assets in a disposable background Blender process.

Example (run serially with other Blender/Unity work):
  blender --background --factory-startup --python-exit-code 1 --python
  tools/profile_rr_export_blender.py -- --source Builder6.blend
  --output D:/Blender/Projects/Build/Recovery/export_profile --mode model all

Never saves the source .blend, registers Unity requests, or writes Unity assets.
The output must be outside every detected/configured Unity project. Each mode
reloads the saved source and exports into a separate output subdirectory.
This profiles per-asset export, not queue clearing or Unity import. Existing
package publication is measured when --reuse-output is explicitly requested.
Native FBX/render work is included in its enclosing stage; nested inclusive
times must not be added together. Exclusive times subtract timed child calls.
"""

import argparse
import ast
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import functools
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback


REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = (
    "Hub_Elevator_A", "Hub_EntranceFrame_Rounded_A",
    "Hub_Floor_Circular_A", "Hub_Wall_Circular_A",
)
FRAMING_PROPERTIES = (
    "icon_zoom", "icon_offset_x", "icon_offset_y", "icon_view_yaw",
    "icon_view_pitch", "icon_light_brightness", "icon_key_light_ratio",
    "icon_fill_light_ratio", "icon_back_light_ratio", "icon_outline_enabled",
    "icon_outline_color", "icon_outline_pixels",
)
EXPORT_PHASES = (
    "export_builder_asset", "_export_builder_asset_contents",
    "validate_standard_output_route", "validate_reference_layout_settings",
    "validate_export_identity", "collect_export_identity_conflicts",
    "live_export_identity_roots", "get_export_asset_meshes", "get_asset_meshes",
    "get_collision_meshes", "mesh_objects_have_export_geometry",
    "build_reference_layout_for_export", "build_group_manifest",
    "build_material_surface_contracts", "export_fbx", "set_active_export_root",
    "prepare_unity_export_maps", "prepare_material_maps_for_unity",
    "cleanup_stale_surface_text_sampling_aliases",
    "create_surface_text_export_meshes", "create_surface_text_sampling_export_aliases",
    "create_surface_text_frame_export_aliases", "build_surface_text_manifest",
    "write_surface_text_source_snapshot", "build_material_map_manifest",
    "copy_image_for_manifest", "current_uv_export_contract",
    "render_or_copy_shared_icon", "render_icon", "apply_icon_outline_to_png",
    "remember_icon_outline_source", "write_manifest", "prepare_framing_for_root",
)
BASELINE_OUTLINE_FUNCTIONS = (
    "apply_icon_outline_to_png", "build_mask_integral", "rectangle_mask_sum",
)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_within(path, directory):
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
        return True
    except ValueError:
        return False


def validate_output(path, unity_project):
    """Resolve existing junctions/symlinks before accepting any output path."""
    path = Path(path).expanduser().resolve()
    if is_within(path, unity_project):
        raise ValueError(f"Profile output must be outside Unity: {path}")
    for ancestor in (path, *path.parents):
        if (ancestor / "Assets").is_dir() and (ancestor / "ProjectSettings").is_dir():
            raise ValueError(f"Profile output is inside a Unity project: {ancestor}")
        # An Assets folder selected before its project finishes initializing is
        # still unsuitable. Be conservative even without ProjectSettings.
        if ancestor.name.casefold() == "assets":
            raise ValueError(f"Profile output cannot be under an Assets folder: {path}")
    return path


def memory_snapshot():
    """Read physical RAM; unavailable values are None, never reported as zero."""
    if os.name != "nt":
        try:
            import psutil
            value = psutil.virtual_memory()
            return {"total_bytes": value.total, "available_bytes": value.available,
                    "used_percent": value.percent, "source": "psutil.virtual_memory"}
        except ImportError:
            return {"total_bytes": None, "available_bytes": None, "used_percent": None,
                    "source": "unavailable"}

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong), ("available_phys", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong), ("available_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended", ctypes.c_ulonglong)]

    value = MemoryStatus()
    value.length = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        return {"total_bytes": None, "available_bytes": None, "used_percent": None,
                "source": "GlobalMemoryStatusEx failed"}
    return {"total_bytes": value.total_phys, "available_bytes": value.available_phys,
            "used_percent": value.load, "source": "GlobalMemoryStatusEx"}


def require_memory(snapshot):
    available = snapshot.get("available_bytes")
    if available is not None and available < 200 * 1024 * 1024:
        raise RuntimeError("Less than 200 MiB physical RAM available; no new export started.")


class PhaseProfiler:
    """One synchronous nesting stack; exclusive time cannot count children twice."""
    def __init__(self):
        self.stack = []
        self.stages = {}

    @contextmanager
    def phase(self, name):
        frame = [name, time.perf_counter(), 0.0]
        self.stack.append(frame)
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = time.perf_counter() - frame[1]
            assert self.stack.pop() is frame, "Unbalanced profiler scope"
            if self.stack:
                self.stack[-1][2] += elapsed
            value = self.stages.setdefault(name, {"calls": 0, "failed_calls": 0,
                                                  "inclusive_seconds": 0.0,
                                                  "exclusive_seconds": 0.0,
                                                  "maximum_call_seconds": 0.0})
            value["calls"] += 1
            value["failed_calls"] += int(failed)
            value["inclusive_seconds"] += elapsed
            value["exclusive_seconds"] += max(0.0, elapsed - frame[2])
            value["maximum_call_seconds"] = max(value["maximum_call_seconds"], elapsed)

    def result(self):
        assert not self.stack, "A profiler call did not finish"
        stages = [{"stage": name, **value} for name, value in self.stages.items()]
        return sorted(stages, key=lambda value: value["exclusive_seconds"], reverse=True)


class SettingsShadow:
    """Read saved RNA settings, shadow framing/output writes without callbacks."""
    def __init__(self, source, output):
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "_overrides", {"output_root": str(output)})

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._source, name)

    def __setattr__(self, name, value):
        self._overrides[name] = value


def primitive(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    try:
        return list(value)
    except TypeError:
        return repr(value)


def render_configuration(scene, settings):
    result = {"scene_engine": scene.render.engine,
              "icon_resolution": getattr(settings, "icon_resolution", None),
              "outline_enabled": getattr(settings, "icon_outline_enabled", None),
              "outline_pixels": getattr(settings, "icon_outline_pixels", None),
              "outline_color": primitive(getattr(settings, "icon_outline_color", None)),
              "cycles_samples": getattr(getattr(scene, "cycles", None), "samples", None)}
    eevee = getattr(scene, "eevee", None)
    result["eevee_samples"] = {name: primitive(getattr(eevee, name))
                               for name in ("taa_render_samples", "taa_samples", "use_shadow_jitter")
                               if eevee is not None and hasattr(eevee, name)}
    return result


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Defaults to <output>/profile.json; must stay under output")
    parser.add_argument("--roots", nargs="+", default=list(DEFAULT_ROOTS))
    parser.add_argument("--mode", nargs="+", choices=("model", "all", "both"), default=["both"])
    parser.add_argument("--baseline-outline", type=Path,
                        help="AST-load only the three historical outline functions; keep current export safety contracts")
    parser.add_argument("--reuse-output", action="store_true",
                        help="Allow existing tool-owned mode folders for repeat-export publication timing")
    return parser.parse_args(argv)


def load_outline_baseline(path, namespace):
    """Compile just named function definitions, binding globals to live RR.

    Top-level imports/expressions from a historical full exporter are not run.
    Its outline therefore still sees the isolated cache and current helpers.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in BASELINE_OUTLINE_FUNCTIONS]
    if sorted(node.name for node in selected) != sorted(BASELINE_OUTLINE_FUNCTIONS):
        raise ValueError("Baseline must define each of the three outline functions exactly once.")
    definitions = {}
    code = compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec")
    exec(code, namespace, definitions)
    return {name: definitions[name] for name in BASELINE_OUTLINE_FUNCTIONS}


def write_report(path, report):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv=None):
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = arguments(argv)
    import bpy
    if not bpy.app.background:
        raise RuntimeError("This tool requires disposable --background --factory-startup Blender.")
    sys.path.insert(0, str(REPOSITORY / "addons"))
    import random_realm_builder_exporter as exporter
    if Path(exporter.__file__).resolve() != (REPOSITORY / "addons/random_realm_builder_exporter/__init__.py").resolve():
        raise RuntimeError("Loaded RR exporter is not the canonical repository source.")

    source = args.source.expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".blend":
        raise ValueError(f"Source must be an existing saved .blend: {source}")
    output = validate_output(args.output, exporter.UNITY_PROJECT_ROOT)
    report_path = args.report.resolve() if args.report else output / "profile.json"
    if not is_within(report_path, output):
        raise ValueError("Report path must stay within the isolated output folder.")
    if report_path.suffix.casefold() != ".json" or report_path == source:
        raise ValueError("Report must be a .json file distinct from the source .blend.")
    modes = list(dict.fromkeys(mode for value in args.mode
                               for mode in (("model", "all") if value == "both" else (value,))))
    baseline_outline = args.baseline_outline.expanduser().resolve() if args.baseline_outline else None
    if baseline_outline is not None and not baseline_outline.is_file():
        raise ValueError(f"Historical outline source is missing: {baseline_outline}")
    marker = output / ".rr-export-profile.json"
    if output.exists() and any(output.iterdir()):
        if not args.reuse_output or not marker.is_file():
            raise ValueError("Output is nonempty; choose a new folder or explicitly reuse tool-owned output.")
        identity = json.loads(marker.read_text(encoding="utf-8"))
        if identity.get("tool") != Path(__file__).name or identity.get("source") != str(source):
            raise ValueError("Existing output marker does not match this tool and source.")
        for entry in output.rglob("*"):
            if not is_within(entry, output):
                raise ValueError(f"Reused output contains a link outside the isolated folder: {entry}")
    source_hash = sha256_file(source)
    memory_before = memory_snapshot()
    require_memory(memory_before)
    output.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"tool": Path(__file__).name, "source": str(source)}, indent=2), encoding="utf-8")
    addon_folder = REPOSITORY / "addons/random_realm_builder_exporter"
    report = {
        "schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running", "source": str(source), "source_sha256_before": source_hash,
        "repository": str(REPOSITORY), "tool_sha256": sha256_file(__file__),
        "addon_version": list(exporter.bl_info["version"]),
        "addon_python_sha256": {str(path.relative_to(addon_folder)): sha256_file(path)
                                 for path in sorted(addon_folder.rglob("*.py"))},
        "blender": {"version": bpy.app.version_string, "binary": bpy.app.binary_path,
                    "background": bpy.app.background},
        "requested_roots": args.roots, "modes": modes, "reuse_output": args.reuse_output,
        "baseline_outline": ({"path": str(baseline_outline), "sha256": sha256_file(baseline_outline),
                              "functions": list(BASELINE_OUTLINE_FUNCTIONS)}
                             if baseline_outline is not None else None),
        "output": str(output), "memory_before": memory_before, "assets": [], "errors": [],
        "blocked_unity_requests": [], "skipped_unity_alias_retirements": [],
        "timing_scope": "Per-root export plus framing. Excludes source loading, setup, source hashing, queue clearing and Unity import.",
        "nested_timing_rule": "inclusive_seconds overlap; exclusive_seconds subtract timed children and can be summed within one asset.",
        "limits": ["Background saved-file snapshot, not current unsaved GUI state.",
                   "First-run and GPU shader-cache warmth depend on mode/root order and system activity.",
                   "Native FBX is inside export_fbx; native render is inside render_icon.",
                   "Canonical identity, quality, materials, modifiers and publication checks are retained.",
                   "Standard Unity legacy-alias retirement is skipped; outline cache is isolated."]}
    write_report(report_path, report)
    current = {"profiler": None}
    restored = []
    registered = False

    def replace(owner, name, value):
        original = getattr(owner, name)
        restored.append((owner, name, original))
        setattr(owner, name, value)

    def block_unity(*call_args, **call_kwargs):
        report["blocked_unity_requests"].append(repr((call_args, call_kwargs)))
        raise RuntimeError("Isolated profile attempted to register a Unity import request.")

    def skip_alias(*call_args, **_kwargs):
        report["skipped_unity_alias_retirements"].append(str(call_args[1]) if len(call_args) > 1 else "unknown")
        return ""

    def instrument(owner, name, label=None):
        if not hasattr(owner, name):
            return
        original = getattr(owner, name)

        @functools.wraps(original)
        def measured(*call_args, **call_kwargs):
            profiler = current["profiler"]
            if profiler is None:
                return original(*call_args, **call_kwargs)
            if name == "write_surface_text_source_snapshot":
                require_memory(memory_snapshot())
            with profiler.phase(label or name):
                return original(*call_args, **call_kwargs)

        replace(owner, name, measured)

    try:
        replace(exporter, "queue_unity_builder_import", block_unity)
        replace(exporter, "retire_standard_flat_model_alias", skip_alias)
        replace(exporter, "UNITY_BUILDER_CACHE_ROOT", str(output / "cache"))
        replace(exporter, "UNITY_BUILDER_ICON_SOURCE_CACHE", str(output / "cache/IconSources"))
        # Keep ordinary publication staging entirely inside the accepted folder.
        replace(exporter, "variant_export_transaction_parent", lambda _root: str(output / "transactions"))
        if baseline_outline is not None:
            for name, function in load_outline_baseline(baseline_outline, exporter.__dict__).items():
                replace(exporter, name, function)
        for name in EXPORT_PHASES:
            instrument(exporter, name)
        for name in ("export_package", "_prepare_package", "prepare_published_metadata"):
            instrument(exporter.rr_standard_export_transaction, name, "publication." + name)
        for name in ("prepare_unity_uvs_for_export", "restore_actions_best_effort"):
            instrument(exporter.rr_unity_uv_export_contract, name, "uv." + name)
        exporter.register()
        registered = True
        for mode in modes:
            require_memory(memory_snapshot())
            bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
            scene = bpy.context.scene
            original_settings = scene.rr_builder_export_settings
            report.setdefault("saved_settings", {"output_root": original_settings.output_root,
                                                   "export_mode": original_settings.export_mode,
                                                   **render_configuration(scene, original_settings)})
            mode_output = output / ("mode-" + mode)
            mode_output.mkdir(exist_ok=True)
            icon_cache = {}
            for root_name in args.roots:
                root = bpy.data.objects.get(root_name)
                item = {"mode": mode, "root": root_name, "output": str(mode_output),
                        "memory_before": memory_snapshot(), "status": "running"}
                report["assets"].append(item)
                profiler = PhaseProfiler()
                shadow = SettingsShadow(original_settings, mode_output)
                settings_before = {name: primitive(getattr(original_settings, name)) for name in FRAMING_PROPERTIES}
                try:
                    require_memory(item["memory_before"])
                    if root is None:
                        raise RuntimeError(f"Requested export root is missing: {root_name}")
                    current["profiler"] = profiler
                    with profiler.phase("whole_root"):
                        exporter.prepare_framing_for_root(root, shadow, item=None)
                        item["configuration"] = render_configuration(scene, shadow)
                        lighting = exporter.rr_icon_lighting.read_profile(root, scene)
                        item["configuration"]["lighting_mode"] = lighting["mode"]
                        item["configuration"]["expected_icon_engine"] = "BLENDER_EEVEE" if lighting["mode"] == "HDRI" else scene.render.engine
                        item["result"] = list(exporter.export_builder_asset(
                            root, shadow, export_model=True, include_icon=(mode == "all"),
                            shared_icon_root=exporter.shared_builder_icon_root(root),
                            queue_import=False, icon_render_cache=icon_cache))
                    item["status"] = "complete"
                except Exception as exception:
                    item["status"] = "failed"
                    item["error"] = str(exception)
                    item["traceback"] = traceback.format_exc()
                    report["errors"].append({"mode": mode, "root": root_name, "error": str(exception)})
                finally:
                    current["profiler"] = None
                    item["stages"] = profiler.result()
                    whole = profiler.stages.get("whole_root", {})
                    item["elapsed_seconds"] = whole.get("inclusive_seconds", 0.0)
                    item["exclusive_seconds_sum"] = sum(value["exclusive_seconds"] for value in item["stages"])
                    item["nested_timing_balanced"] = abs(item["exclusive_seconds_sum"] - item["elapsed_seconds"]) < 0.000001
                    settings_after = {name: primitive(getattr(original_settings, name)) for name in FRAMING_PROPERTIES}
                    item["saved_framing_unchanged"] = settings_before == settings_after
                    if not item["saved_framing_unchanged"]:
                        report["errors"].append({"mode": mode, "root": root_name, "error": "Saved framing changed despite shadow settings."})
                    item["memory_after"] = memory_snapshot()
                    write_report(report_path, report)
                    print("[RR Export Profile]", mode, root_name, item["status"], f"{item['elapsed_seconds']:.3f}s", flush=True)
    except Exception as exception:
        report["errors"].append({"error": str(exception), "traceback": traceback.format_exc()})
    finally:
        current["profiler"] = None
        if registered:
            try:
                exporter.unregister()
            except Exception as exception:
                report["errors"].append({"error": "Unregister: " + str(exception)})
        for owner, name, original in reversed(restored):
            setattr(owner, name, original)
        report["source_sha256_after"] = sha256_file(source)
        report["source_disk_unchanged"] = report["source_sha256_after"] == source_hash
        if not report["source_disk_unchanged"]:
            report["errors"].append({"error": "Source file hash changed; check for a concurrent external save."})
        report["memory_after"] = memory_snapshot()
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report["status"] = "failed" if report["errors"] else "complete"
        report["mode_totals_seconds"] = {mode: sum(value["elapsed_seconds"] for value in report["assets"] if value["mode"] == mode) for mode in modes}
        write_report(report_path, report)
    print("[RR Export Profile] Report:", report_path, flush=True)
    if report["errors"]:
        raise RuntimeError("Isolated RR export profile failed; see its JSON report.")


if __name__ == "__main__":
    main()
