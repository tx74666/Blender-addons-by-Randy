"""Small native Direct/Actual Surface physics-permission checks.

Run only in a fresh, isolated background Blender through the trusted launcher.
Example script arguments: --addon-root <frozen>/character_designer --output <evidence>/surface.json
No artist file is opened. Only this script's independently named private blend
is saved/reopened. Existing test helpers supply a three-bone rig and 4x16 Dress.
This checks native endpoints, modes, author inputs and cache configuration;
it does not prove collision quality, cached simulation payload, playback or FPS.
"""

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import traceback

import bpy


TESTS = Path(__file__).resolve().parent
BACKENDS = {"DIRECT": "DIRECT_MAIN_CLOTH_V1", "ACTUAL": "ACTUAL_SURFACE_DELTA_V1"}


def require(value, message):
    if not value:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addon-root", type=Path, required=True,
                        help="Explicit frozen character_designer package, not its parent")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backends", choices=tuple(BACKENDS), nargs="+", default=list(BACKENDS))
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    args.addon_root, args.output = args.addon_root.resolve(), args.output.resolve()
    require(args.addon_root.name == "character_designer" and (args.addon_root / "__init__.py").is_file(),
            "Select an explicit Character Designer package.")
    require(len(args.backends) == len(set(args.backends)), "Repeated backends are refused.")
    require(not args.output.is_relative_to(args.addon_root), "Keep evidence outside the package.")
    require(not args.output.exists(), "Use a fresh evidence output; earlier reports are preserved.")
    require(bpy.app.background and not bpy.data.filepath,
            "Use a fresh empty background Blender; no saved artist input is accepted.")
    require(bpy.app.version >= (5, 2, 0), "This native socket check requires Blender 5.2 or newer.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    return args


def primitive_mesh(obj):
    mesh = obj.data
    return {
        "vertices": [list(vertex.co) for vertex in mesh.vertices],
        "edges": [list(edge.vertices) for edge in mesh.edges],
        "faces": [list(face.vertices) for face in mesh.polygons],
        "groups": [(group.name, group.lock_weight) for group in obj.vertex_groups],
        "weights": [sorted((entry.group, entry.weight) for entry in vertex.groups)
                    for vertex in mesh.vertices],
        "keys": None if mesh.shape_keys is None else [
            (key.name, key.value, [list(point.co) for point in key.data])
            for key in mesh.shape_keys.key_blocks],
    }


def protected(source, body, rig):
    return {"source": primitive_mesh(source), "body": primitive_mesh(body),
            "rest": [(bone.name, bone.parent.name if bone.parent else None, bone.use_deform,
                      [list(row) for row in bone.matrix_local]) for bone in rig.data.bones],
            "pose": [(bone.name, bone.rotation_mode, list(bone.location), list(bone.rotation_euler),
                      list(bone.rotation_quaternion), list(bone.rotation_axis_angle), list(bone.scale))
                     for bone in rig.pose.bones],
            "actions": [(action.name, action.use_fake_user) for action in bpy.data.actions],
            "source_record": source.get("character_designer_skirt_v1")}


def cache_configuration(cloth):
    cache = cloth.point_cache
    return {key: getattr(cache, key) for key in (
        "is_baked", "is_baking", "use_external", "use_disk_cache", "filepath",
        "frame_start", "frame_end", "frame_step", "name", "index")}


def cache_observation(cloth):
    return dict(cache_configuration(cloth), info=cloth.point_cache.info,
                is_outdated=cloth.point_cache.is_outdated)


def endpoints(source, skirt, physics, direct):
    record, rig, actual, cloth = physics.validate_physics(source)
    result = {"backend": record["physics"]["backend"],
              "cloth_flags": [cloth.show_viewport, cloth.show_render],
              "mode": json.loads(source["character_designer_dress_motion_v1"])["mode"],
              "cache": cache_observation(cloth)}
    if result["backend"] == BACKENDS["DIRECT"]:
        output = direct._overlay(source, record)
        socket = direct._mode_input(output, record["physics"]["surface"]["mode_socket"])
        result.update(socket_type=socket.type, socket_value=socket.value,
                      socket_is_native_bool=type(socket.value) is bool,
                      direct_state=source[direct.STATE_KEY],
                      output_flags=[output.show_viewport, output.show_render])
    return record, rig, actual, cloth, result


def make_fixture(backend, skirt, physics, activate, character, frustum):
    rig = character()
    bpy.context.scene.character_designer_setup.rig = rig
    source = frustum("Physics Surface Dress", rows=4, sides=16)
    activate(source)
    skirt.build_skirt(bpy.context, source, chain_count=4, segment_count=3,
                      armature=rig, parent_bone="Hips", shared=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=4, location=(0., 0., 1.7),
                                       scale=(.3, .25, .4))
    body = bpy.context.object
    body.name = "Physics Surface Body"
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    group = body.vertex_groups.new(name="Hips")
    group.add(list(range(len(body.data.vertices))), 1., "REPLACE")
    body.shape_key_add(name="Basis")
    body.shape_key_add(name="Artist Body Shape").data[0].co.x += .01
    body.data.shape_keys.key_blocks[1].value = .25
    armature = body.modifiers.new("Body Armature", "ARMATURE")
    armature.object = rig
    activate(source)
    physics.add_physics(bpy.context, source, backend=BACKENDS[backend], body=body, capability="BOTH")
    return source, body, rig


def run_case(args, backend, modules, helpers):
    cd, gate, skirt, physics, profiles, tuning, direct = modules
    activate, character, frustum = helpers
    result = {"backend": BACKENDS[backend], "passed": False, "stage": "empty-fixture", "checks": {}}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        temporary = args.output.parent / ("temporary_" + backend.lower())
        temporary.mkdir(exist_ok=True)
        bpy.context.preferences.filepaths.temporary_directory = str(temporary)
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 2
        bpy.context.scene.frame_set(1)
        result["stage"] = "native-install"
        source, body, rig = make_fixture(backend, skirt, physics, *helpers)
        record, _rig, actual, cloth, automatic = endpoints(source, skirt, physics, direct)
        require(automatic["mode"] == "AUTOMATIC" and automatic["cloth_flags"] == [True, True],
                "The installed Automatic endpoint is not active.")
        if backend == "DIRECT":
            require(automatic["socket_is_native_bool"] and automatic["socket_value"] is True,
                    "The Direct Automatic output is not a native boolean True.")
        original, raw_profile = protected(source, body, rig), source[profiles.PROFILE_KEY]
        cache = cache_configuration(cloth)
        result["automatic_before"] = automatic
        result["stage"] = "automatic-off-on"
        gate.set_enabled(bpy.context, False)
        _record, _rig, _actual, off_cloth, paused = endpoints(source, skirt, physics, direct)
        require(paused["cloth_flags"] == [False, False], "Master Off did not stop the native Cloth modifier.")
        if backend == "DIRECT":
            require(paused["socket_is_native_bool"] and paused["socket_value"] is False,
                    "Master Off did not gate the native Direct output socket.")
        require(source[profiles.PROFILE_KEY] == raw_profile, "Master Off changed the saved mode/profile.")
        require(protected(source, body, rig) == original, "Master Off changed protected author inputs.")
        require(cache_configuration(off_cloth) == cache, "Master Off changed the cache configuration.")
        gate.set_enabled(bpy.context, True)
        _record, _rig, _actual, cloth, resumed = endpoints(source, skirt, physics, direct)
        require(resumed["cloth_flags"] == automatic["cloth_flags"], "Master On did not restore Automatic Cloth flags.")
        if backend == "DIRECT":
            require(resumed["socket_value"] == automatic["socket_value"], "Master On did not restore the Direct output.")
            require(resumed["direct_state"] == automatic["direct_state"], "Master Off/On changed Direct mode readiness.")
        require(source[profiles.PROFILE_KEY] == raw_profile and protected(source, body, rig) == original,
                "Automatic Off/On changed mode or protected inputs.")
        require(cache_configuration(cloth) == cache, "Automatic Off/On changed the cache configuration.")
        result["checks"]["automatic_native_endpoints_mode_author_cache"] = True
        result["automatic_paused"], result["automatic_resumed"] = paused, resumed
        result["stage"] = "manual-off-on"
        gate.set_skirt_mode(bpy.context, source.name, "MANUAL", expected_owner=record["owner"])
        _record, _rig, _actual, cloth, manual = endpoints(source, skirt, physics, direct)
        require(manual["mode"] == "MANUAL", "The existing Manual mode did not apply.")
        manual_inputs, manual_profile, manual_cache = protected(source, body, rig), source[profiles.PROFILE_KEY], cache_configuration(cloth)
        gate.set_enabled(bpy.context, False)
        _record, _rig, _actual, cloth, manual_paused = endpoints(source, skirt, physics, direct)
        require(manual_paused["cloth_flags"] == [False, False], "Manual branch still evaluates Cloth after Master Off.")
        gate.set_enabled(bpy.context, True)
        _record, _rig, _actual, cloth, manual_resumed = endpoints(source, skirt, physics, direct)
        require(manual_resumed["mode"] == "MANUAL" and manual_resumed["cloth_flags"] == manual["cloth_flags"],
                "Master On changed the existing Manual mode or its native endpoint.")
        if backend == "DIRECT":
            require(manual_resumed["socket_value"] is False and manual_resumed["direct_state"] == manual["direct_state"],
                    "Master On reopened Direct Cloth or changed the Manual pending state.")
        require(source[profiles.PROFILE_KEY] == manual_profile and protected(source, body, rig) == manual_inputs,
                "Manual Off/On changed mode or protected inputs.")
        require(cache_configuration(cloth) == manual_cache, "Manual Off/On changed cache configuration.")
        result["checks"]["manual_native_endpoints_mode_author_cache"] = True
        result["manual_before"], result["manual_resumed"] = manual, manual_resumed
        result["stage"] = "private-save-reopen-paused"
        gate.set_enabled(bpy.context, False)
        private = args.output.parent / ("physics_surface_" + backend.lower() + "_private.blend")
        require(not private.exists(), "Use a fresh evidence folder; earlier private scenes are preserved.")
        saved_names = source.name, body.name, rig.name
        require("FINISHED" in bpy.ops.wm.save_as_mainfile(filepath=str(private), check_existing=False), "Private save failed.")
        pin = {"path": str(private), "bytes": private.stat().st_size, "sha256": digest(private)}
        bpy.ops.wm.open_mainfile(filepath=str(private), use_scripts=True)
        source, body, rig = (bpy.data.objects[name] for name in saved_names)
        require(not gate.is_enabled(source, component="SKIRT"), "Saved master permission did not remain off.")
        _record, _rig, _actual, cloth, reopened = endpoints(source, skirt, physics, direct)
        require(reopened["cloth_flags"] == [False, False], "Reopened paused Cloth is active.")
        require(source[profiles.PROFILE_KEY] == manual_profile and protected(source, body, rig) == manual_inputs,
                "Serialized source mode or author inputs changed.")
        gate.set_enabled(bpy.context, True)
        _record, _rig, _actual, cloth, final = endpoints(source, skirt, physics, direct)
        require(final["mode"] == "MANUAL" and final["cloth_flags"] == manual["cloth_flags"], "Reopened resume changed Manual.")
        require(cache_configuration(cloth) == manual_cache, "Serialized cache configuration changed.")
        result["checks"]["private_paused_save_reopen_resume"] = True
        result.update(passed=True, stage="complete", private=pin, reopened=reopened, final=final,
                      vertices=len(source.data.vertices), original_mode=automatic["mode"], final_mode=final["mode"])
    except Exception as error:
        result.update(error=str(error), traceback=traceback.format_exc())
    return result


def main():
    args = arguments()
    sys.path[:0] = [str(args.addon_root.parent), str(TESTS)]
    cd = importlib.import_module("character_designer")
    require(Path(cd.__file__).resolve().parent == args.addon_root, "The loaded package is not the explicit source.")
    names = ("animation_physics", "skirt_rig", "skirt_physics", "skirt_motion_profiles",
             "skirt_motion_tuning", "skirt_surface_direct")
    modules = (cd,) + tuple(importlib.import_module("character_designer." + name) for name in names)
    from test_skirt_physics_blender import activate, character
    from test_skirt_topology_blender import frustum
    result = {"schema": "character-designer.animation-physics.native-surface-fixture/1",
              "passed": False, "blender": bpy.app.version_string,
              "trusted_launcher_enable_autoexec_required": True, "private_reopen_use_scripts": True,
              "observed_autoexec_preference": getattr(bpy.context.preferences.filepaths, "use_scripts_auto_execute", None),
              "source": str(args.addon_root), "cases": [],
              "limits": ["Small 4x16 synthetic fixture, not the artist model or collision-quality acceptance.",
                         "No forward playback, render, FPS or simulation cache-payload preservation proof.",
                         "Cache configuration and its observed native status are recorded separately."],
              "helpers": {name: digest(TESTS / name) for name in
                          ("test_skirt_physics_blender.py", "test_skirt_topology_blender.py")}}
    registered = False
    try:
        cd.register()
        registered = True
        for backend in args.backends:
            result["cases"].append(run_case(args, backend, modules, (activate, character, frustum)))
        result["passed"] = all(case["passed"] for case in result["cases"])
    except Exception as error:
        result.update(error=str(error), traceback=traceback.format_exc())
    finally:
        if registered:
            try:
                cd.unregister()
                result["normal_unregister"] = True
            except Exception as error:
                result.update(passed=False, unregister_error=str(error))
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf8")
    print(json.dumps({"passed": result["passed"], "output": str(args.output)}), flush=True)
    if not result["passed"]:
        raise RuntimeError("Native surface fixture failed; preserve its stage-specific evidence.")


if __name__ == "__main__":
    main()
