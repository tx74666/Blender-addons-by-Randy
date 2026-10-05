"""Prepared native collider regression; run only in a disposable factory child.

Requires an explicit installed Dress QA blend under X/Validation. It checks
native coordinate/volume rejection, explicit v1 migration/rollback, Body shared
data protection, and save/reopen. No forward simulation, render or Unity work.
"""
import argparse
import copy
import hashlib
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = Path(r"D:\Blender\Projects\Character\X\Validation").resolve()
sys.path.insert(0, str(ROOT / "addons"))
import character_designer
from character_designer import skirt, skirt_rig, skirt_surface as surface


def require(condition, message):
    if not condition: raise AssertionError(message)


def selection_state():
    active = bpy.context.view_layer.objects.active
    return {"active": active.name if active else None, "mode": active.mode if active else "OBJECT",
            "selected": sorted(obj.name for obj in bpy.context.selected_objects),
            "flags": {obj.name: [obj.hide_get(), obj.hide_select] for obj in bpy.context.view_layer.objects}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", default="Dress")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    args.input, args.output = args.input.resolve(), args.output.resolve()
    require(bpy.app.background and "--factory-startup" in sys.argv and not bpy.data.filepath,
            "Use an empty factory background child, never the artist window")
    require(args.input.is_file() and args.input.suffix.casefold() == ".blend"
            and args.input.is_relative_to(VALIDATION), "Input must be an independent saved X Validation QA")
    require(args.output.is_relative_to(VALIDATION) and not args.output.exists()
            and not args.input.is_relative_to(args.output), "Output must be a new independent Validation directory")
    initial_disk = hashlib.sha256(args.input.read_bytes()).hexdigest()
    args.output.mkdir(parents=True)
    character_designer.register()
    bpy.ops.wm.open_mainfile(filepath=str(args.input), load_ui=False, use_scripts=False)
    source = bpy.data.objects[args.source]
    rig = source[skirt_rig.RIG_KEY]
    record = skirt_rig.read_record(source)
    actual, cloth = surface.validate(source, rig, record)
    require(not cloth.point_cache.is_baked and not cloth.point_cache.is_baking
            and not cloth.point_cache.use_external and not record["physics"].get("baked_range"),
            "Use an unsealed installed QA; this test never resets existing cache")
    body = surface._object(record, "BODY_ATTACHMENT", source)
    upstream = bpy.data.objects[record["physics"]["surface"]["body"]]
    body_before = surface._digest({"coords": [list(vertex.co) for vertex in upstream.data.vertices],
                                  "topology": surface._topology(upstream.data), "groups": surface._groups(upstream)})
    require(body.data == upstream.data, "Body collision must keep a live shared Mesh")
    # Exercise explicit old-contract migration even when the supplied QA already
    # has v2. This declared representation exists only in this disposable child;
    # all native coordinates, topology, weights and bindings have just passed.
    record = copy.deepcopy(record)
    record["physics"]["surface"]["colliders"] = {name: surface._helper_contract(bpy.data.objects[name])
        for name in record["physics"]["colliders"][:-1]}
    skirt_rig.write_record(source, record)
    body.hide_select = False
    surface.validate(source, rig, record)
    settings = bpy.context.window_manager.character_designer_skirt
    settings.source = source
    if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in bpy.context.selected_objects: obj.select_set(False)
    source.hide_set(False)
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.context.view_layer.update()
    selected_before = selection_state()
    raw_before = source[skirt_rig.RECORD_KEY]
    original_write = skirt_rig.write_record
    def write_then_fail(*args):
        original_write(*args)
        raise RuntimeError("Native QA deliberate post-migration write failure")
    skirt_rig.write_record = write_then_fail
    try:
        try:
            result = bpy.ops.character_designer.skirt_select_colliders()
            require(result == {"CANCELLED"}, "Late injected error must cancel")
        except RuntimeError as error:
            require("deliberate post-migration" in str(error), "Unexpected native operator failure")
    finally:
        skirt_rig.write_record = original_write
    require(source[skirt_rig.RECORD_KEY] == raw_before and selection_state() == selected_before,
            "Late migration failure must restore metadata, selection, mode and all helper flags")
    require(bpy.ops.character_designer.skirt_select_colliders() == {"FINISHED"}, "Native Select Colliders failed")
    updated = skirt_rig.read_record(source)
    colliders = skirt._colliders(updated)
    require(len(colliders) == 3 and set(bpy.context.selected_objects) == set(colliders)
            and body not in colliders and body.hide_select, "Only independent pelvis/thigh fitting meshes may be selected")
    require(all(item["version"] == surface.COLLIDER_CONTRACT_VERSION
                for item in updated["physics"]["surface"]["colliders"].values()), "Explicit v2 migration did not complete")
    collider = colliders[0]
    for vertex in collider.data.vertices:
        vertex.co.x *= 1.01
        vertex.co.y *= 1.01
    collider.data.update()
    bpy.context.view_layer.update()
    surface.validate(source, rig, updated)
    fitted = [vertex.co.copy() for vertex in collider.data.vertices]
    for vertex in collider.data.vertices: vertex.co *= -1
    collider.data.update()
    try:
        surface.validate(source, rig, updated)
    except ValueError as error:
        require("zero volume or inverted winding" in str(error), "Inverted coordinates failed for an unrelated reason")
    else:
        raise AssertionError("Native negative-volume fitting was accepted")
    finally:
        for vertex, coordinate in zip(collider.data.vertices, fitted): vertex.co = coordinate
        collider.data.update()
    collider.data.vertices[0].co.x = float("nan")
    collider.data.update()
    try:
        surface.validate(source, rig, updated)
    except ValueError as error:
        require("invalid coordinates" in str(error), "Nonfinite coordinates failed for an unrelated reason")
    else:
        raise AssertionError("Native nonfinite fitting was accepted")
    finally:
        collider.data.vertices[0].co = fitted[0]
        collider.data.update()
    saved = args.output / "Cosha_Dress_QA_collider_fit.blend"
    require("FINISHED" in bpy.ops.wm.save_as_mainfile(filepath=str(saved), check_existing=False), "QA save failed")
    require("FINISHED" in bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False, use_scripts=False), "QA reopen failed")
    source = bpy.data.objects[args.source]
    reopened = skirt_rig.read_record(source)
    surface.validate(source, source[skirt_rig.RIG_KEY], reopened)
    body = surface._object(reopened, "BODY_ATTACHMENT", source)
    upstream = bpy.data.objects[reopened["physics"]["surface"]["body"]]
    require(body.hide_select and body.data == upstream.data, "Read-only Body protection did not persist")
    require(body_before == surface._digest({"coords": [list(vertex.co) for vertex in upstream.data.vertices],
        "topology": surface._topology(upstream.data), "groups": surface._groups(upstream)}), "Shared artist Body data changed")
    require(hashlib.sha256(args.input.read_bytes()).hexdigest() == initial_disk, "Input QA bytes changed")
    print("SKIRT_COLLIDER_FITTING_NATIVE_PASSED", flush=True)


if __name__ == "__main__": main()
