"""Same-time Unity/Blender evidence on a disposable copy of a real character.

blender --background --factory-startup --python this.py -- --blend ... --motion ... --report ...
Never writes the input blend. A separate review blend and pose images are artifacts.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
import character_designer
from character_designer import unity_animation as ua, forearm_twist as ft


def vector(value):
    return Vector((value["x"], value["y"], value["z"])) if isinstance(value, dict) else Vector(value)


def source_fingerprint(rig, meshes):
    value = {
        "rest": [(b.name, b.parent.name if b.parent else None, [v for row in b.matrix_local for v in row], b.use_deform) for b in rig.data.bones],
        "meshes": [(obj.name, [(tuple(v.co), [(g.group, g.weight) for g in v.groups]) for v in obj.data.vertices],
                    [(key.name, key.value, [tuple(p.co) for p in key.data]) for key in obj.data.shape_keys.key_blocks
                     if not key.name.startswith("CD Forearm")] if obj.data.shape_keys else [],
                    [(m.name, m.type, m.show_viewport) for m in obj.modifiers]) for obj in meshes],
    }
    return hashlib.sha256(repr(value).encode()).hexdigest()


def positions(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def set_sample(scene, rig, time, fps):
    ua._set_frame(scene, 1 + time * fps)
    bpy.context.view_layer.update()
    ft.update_runtime(scene, bpy.context.evaluated_depsgraph_get())
    bpy.context.view_layer.update()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--motion", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    before_file = hashlib.sha256(Path(args.blend).read_bytes()).hexdigest()
    bpy.ops.wm.open_mainfile(filepath=args.blend, use_scripts=False, load_ui=False)
    character_designer.register()
    rig, scene = bpy.data.objects["CoshaRig"], bpy.context.scene
    meshes = [obj for obj in scene.objects if obj.type == "MESH" and any(m.type == "ARMATURE" and m.object == rig for m in obj.modifiers)]
    before = source_fingerprint(rig, meshes)
    original_data = rig.data
    snapshot = ua._snapshot(bpy.context, rig)
    previous = rig.animation_data.action if rig.animation_data else None
    data = ua.load_package(args.motion)
    mapping = ua._mapping(rig, data, scene.unit_settings.scale_length)
    # Build point correspondences on the real, evaluated rest mesh, never guessed circles.
    if rig.animation_data:
        rig.animation_data.action = None
        rig.animation_data.use_nla = False
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    ft.update_runtime(scene, bpy.context.evaluated_depsgraph_get())
    bpy.context.view_layer.update()
    correspondence, mesh_report = {}, {}
    for definition in data.get("meshes", []):
        obj = bpy.data.objects.get(definition["name"])
        if obj not in meshes:
            continue
        coords = positions(obj)
        kd = KDTree(len(coords))
        for index, co in enumerate(coords):
            kd.insert(co, index)
        kd.balance()
        matches, maximum = [], 0.0
        for point in definition["restPositions"]:
            co = mapping.conversion @ vector(point)
            _, index, distance = kd.find(co)
            matches.append(index)
            maximum = max(maximum, distance)
        correspondence[obj.name] = matches
        mesh_report[obj.name] = {"unity_vertices": len(matches), "blender_evaluated_vertices": len(coords),
                                 "maximum_rest_distance_m": maximum, "pose_samples": []}
    ua._restore_snapshot(bpy.context, rig, snapshot, previous)
    result = ua.import_test_action(bpy.context, rig, args.motion)
    fps = scene.render.fps / scene.render.fps_base
    max_position, max_angle, worst_bone = 0.0, 0.0, None
    skin_samples = []
    for index, frame in enumerate(data["frames"]):
        set_sample(scene, rig, frame["time"], fps)
        expected = ua.expected_world_matrices(rig, data, index, mapping=mapping)
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        for name, desired in expected.items():
            actual = rig.matrix_world @ evaluated.pose.bones[name].matrix
            pos = (actual.translation - desired.translation).length * scene.unit_settings.scale_length
            angle = actual.to_quaternion().rotation_difference(desired.to_quaternion()).angle
            angle = math.degrees(min(angle, abs(math.tau - angle)))
            if pos > max_position or angle > max_angle:
                worst_bone = {"bone": name, "time": frame["time"], "position_m": pos, "angle_degrees": angle}
            max_position, max_angle = max(max_position, pos), max(max_angle, angle)
        for mesh_pose in frame.get("meshes") or []:
            name = mesh_pose["name"]
            if name not in correspondence:
                continue
            obj = bpy.data.objects[name]
            coords = positions(obj)
            errors = [(coords[dest] - mapping.conversion @ vector(point)).length for dest, point in zip(correspondence[name], mesh_pose["positions"])]
            sample = {"time": frame["time"], "maximum_distance_m": max(errors, default=0),
                      "rms_distance_m": math.sqrt(sum(e*e for e in errors) / max(1, len(errors)))}
            mesh_report[name]["pose_samples"].append(sample)
            skin_samples.append((index, name, coords))
    # The same frame must not depend on seek direction, including actual source skin.
    repeat = 0.0
    for index, name, original in reversed(skin_samples):
        set_sample(scene, rig, data["frames"][index]["time"], fps)
        coords = positions(bpy.data.objects[name])
        repeat = max(repeat, max(((a-b).length for a,b in zip(original, coords)), default=0))
    # Keep a standalone review file with the test Action and its persistent Restore record.
    set_sample(scene, rig, data["duration"] * .5, fps)
    settings = bpy.context.window_manager.character_designer_animation
    settings.target = rig
    settings.unity_directory = str(Path(args.motion).parent)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    rig.hide_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    artifact = report_path.with_name("Cosha_Walk_review.blend")
    bpy.ops.wm.save_as_mainfile(filepath=str(artifact), copy=True)
    forearm_errors = {obj.name: ft._ERRORS[obj.name] for obj in meshes if obj.name in ft._ERRORS}
    ua.restore_preview(bpy.context, rig)
    restored = ua._snapshot(bpy.context, rig) == snapshot
    unchanged = source_fingerprint(rig, meshes) == before
    file_unchanged = before_file == hashlib.sha256(Path(args.blend).read_bytes()).hexdigest()
    report = {"source": args.blend, "source_sha256": before_file, "motion": args.motion,
              "samples": result.sample_count, "bones": len(mapping.indices), "mapping_error_m": mapping.error_metres,
              "maximum_bone_position_error_m": max_position, "maximum_bone_rotation_error_degrees": max_angle,
              "worst_bone": worst_bone, "meshes": mesh_report, "reverse_seek_skin_error_m": repeat,
              "restored_session": restored, "original_geometry_rest_weights_artist_shapes_unchanged": unchanged,
              "input_file_unchanged": file_unchanged, "review_blend": str(artifact),
              "original_armature_data_restored": rig.data == original_data,
              "forearm_errors": forearm_errors,
              "acceptance_scope": "Bone transforms, repeatable source skin and reversible Action import. Surface distances are diagnostic nearest-rest correspondences, not an exact topology match.",
              "note": "See separate skin diagnostics for source topology mismatch, subdivision order and Unity skin influence limit. No geometry, weight or calibration correction is applied."}
    report["passed"] = max_position < .0001 and max_angle < .1 and repeat < .00001 and restored and unchanged and file_unchanged
    report["full_current_character_acceptance"] = report["passed"] and not forearm_errors
    report_path.write_text(json.dumps(report, indent=2), encoding="utf8")
    print(json.dumps({k:v for k,v in report.items() if k != "meshes"}))
    if not report["passed"]:
        raise AssertionError("Character validation did not pass; see " + str(report_path))


if __name__ == "__main__":
    main()
