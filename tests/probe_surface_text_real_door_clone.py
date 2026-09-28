"""Explicit fixture-only face selection/rebind. Never edits the authored Builder6 file.

Run in a fresh background Blender with -- --output-dir ABSOLUTE_DIR.
The guessed test region and all evidence are logged; this is NOT production migration.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_surface_text_editable_contract_blender import surface, write_fixture


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    directory = Path(args[args.index("--output-dir") + 1]).resolve()
    source_path = Path("D:/Blender/Projects/Build/WIP/Builder6.blend")
    clone_path = directory / "source-clone.blend"
    if source_path.resolve() == clone_path.resolve() or directory.exists() and any(directory.iterdir()):
        raise RuntimeError("Use a fresh external fixture directory, never the authoring source directory.")
    directory.mkdir(parents=True, exist_ok=True)
    original_hash = sha256(source_path)
    shutil.copy2(source_path, clone_path)
    report = {"fixtureOnly": True, "source": str(source_path), "sourceSha256": original_hash,
              "selectionPolicy": "Fixture-only geometric selection around the already authored Font; never automatic production migration."}
    try:
        bpy.ops.wm.open_mainfile(filepath=str(clone_path))
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        root = bpy.data.objects.get("Hub_EntranceFrame_Rounded_A")
        text = bpy.data.objects.get("Surface Text")
        if root is None or text is None or text.type != "FONT":
            raise RuntimeError("The expected real door and authored Surface Text were not found.")
        original_pose = list(value for row in text.matrix_world for value in row)
        original_body = text.data.body
        target = surface._surface_text_target(text)
        if target is None:
            raise RuntimeError("The authored text has no Shrinkwrap target.")
        report["target"] = target.name
        report["font"] = text.name
        report["body"] = original_body
        report["appliedModifiersInCloneOnly"] = [modifier.name for modifier in target.modifiers]
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = target.evaluated_get(depsgraph)
        evaluated_mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
        target.data = evaluated_mesh
        for modifier in list(target.modifiers):
            target.modifiers.remove(modifier)
        bpy.context.view_layer.update()

        inverse_font = text.matrix_world.inverted()
        normal_matrix = target.matrix_world.to_3x3().inverted().transposed()
        expected_normal = text.matrix_world.to_3x3().col[2].normalized()
        bounds = [Vector(point) for point in text.bound_box]
        min_x, max_x = min(point.x for point in bounds), max(point.x for point in bounds)
        min_y, max_y = min(point.y for point in bounds), max(point.y for point in bounds)
        margin = max(float(text.data.size) * 0.5, 0.05)
        evidence = []
        for polygon in target.data.polygons:
            center = target.matrix_world @ polygon.center
            local = inverse_font @ center
            alignment = (normal_matrix @ polygon.normal).normalized().dot(expected_normal)
            if min_x - margin <= local.x <= max_x + margin and min_y - margin <= local.y <= max_y + margin and alignment > 0.5:
                evidence.append({"index": polygon.index, "fontLocalCenter": list(local),
                                 "worldCenter": list(center), "normalDot": alignment})
        if not evidence:
            raise RuntimeError("No defensible fixture face candidate under the authored Font; inspect the clone manually.")
        nearest_depth = min(abs(item["fontLocalCenter"][2]) for item in evidence)
        chosen = {item["index"] for item in evidence if abs(item["fontLocalCenter"][2]) <= nearest_depth + max(margin, 0.1)}
        bm = bmesh.new()
        try:
            bm.from_mesh(target.data)
            bm.faces.ensure_lookup_table()
            bm.normal_update()
            seed = min(chosen, key=lambda index: (inverse_font @ target.matrix_world @ target.data.polygons[index].center).length_squared)
            component, pending = {seed}, [seed]
            while pending:
                face = bm.faces[pending.pop()]
                for edge in face.edges:
                    for adjacent in edge.link_faces:
                        if adjacent.index in chosen and adjacent.index not in component:
                            component.add(adjacent.index)
                            pending.append(adjacent.index)
            region = surface._selected_surface_info(target, [bm.faces[index] for index in sorted(component)])
            region["candidate_label"] = "Original"
        finally:
            bm.free()
        report["selectedFaceIndices"] = sorted(component)
        report["candidateEvidence"] = evidence
        report["fontLocalBoundsXY"] = [min_x, max_x, min_y, max_y]
        report["selectionMargin"] = margin
        surface.bind_existing_surface_text(bpy.context, text, target, region)
        if list(value for row in text.matrix_world for value in row) != original_pose or text.data.body != original_body:
            raise RuntimeError("Clone binding changed the authored text or pose.")
        bpy.ops.wm.save_as_mainfile(filepath=str(clone_path), check_existing=False)
        write_fixture(directory, root)
        report["success"] = True
    except BaseException as exception:
        report["success"] = False
        report["error"] = str(exception)
        raise
    finally:
        report["originalSourceUnchanged"] = sha256(source_path) == original_hash
        (directory / "selection-evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not report["originalSourceUnchanged"]:
            raise RuntimeError("The original authoring file changed during fixture preparation; investigate before continuing.")


if __name__ == "__main__":
    main()
