"""Read-only naming migration validation against an existing saved .blend.

Run in an isolated Blender background process with factory preferences:
    blender --background --factory-startup --disable-autoexec --python this.py -- input.blend [report.json] [--expect-clean]
The source is opened, inspected and changed only in memory; it is never saved.
The optional flag verifies an already-cleaned file without permitting renames.
"""
import array
import hashlib
import json
from pathlib import Path
import struct
import sys
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))


def pointer(value):
    return value.as_pointer() if value is not None else 0


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest(value):
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def matrix(value):
    return tuple(tuple(float(component) for component in row) for row in value)


def rna_values(value, depth=1):
    """Capture authored RNA values, excluding transient readonly evaluation flags."""
    result = []
    for prop in value.bl_rna.properties:
        identifier = prop.identifier
        if identifier == "rna_type" or (prop.is_readonly and prop.type != "COLLECTION"):
            continue
        try:
            item = getattr(value, identifier)
        except (AttributeError, RuntimeError, TypeError):
            continue
        if prop.type == "POINTER":
            saved = pointer(item)
        elif prop.type == "COLLECTION":
            if depth < 1:
                continue
            saved = tuple(rna_values(child, depth - 1) for child in item)
        elif getattr(prop, "is_array", False):
            saved = tuple(item)
        elif isinstance(item, set):
            saved = tuple(sorted(item))
        else:
            saved = item
        result.append((identifier, saved))
    return tuple(result)


def array_hash(collection, field, width, typecode):
    values = array.array(typecode, [0]) * (len(collection) * width)
    collection.foreach_get(field, values)
    return hashlib.sha256(values.tobytes()).hexdigest()


def mesh_state(mesh):
    weights = hashlib.sha256()
    for vertex in mesh.vertices:
        weights.update(struct.pack("<II", vertex.index, len(vertex.groups)))
        for group in vertex.groups:
            weights.update(struct.pack("<Id", group.group, group.weight))
    return {
        "vertices": (len(mesh.vertices), array_hash(mesh.vertices, "co", 3, "f")),
        "edges": (len(mesh.edges), array_hash(mesh.edges, "vertices", 2, "i")),
        "loops": (len(mesh.loops), array_hash(mesh.loops, "vertex_index", 1, "i")),
        "polygons": (len(mesh.polygons), array_hash(mesh.polygons, "loop_start", 1, "i"),
                     array_hash(mesh.polygons, "loop_total", 1, "i"),
                     array_hash(mesh.polygons, "material_index", 1, "i")),
        "weights": weights.hexdigest(),
        "shape_keys": pointer(mesh.shape_keys),
        "materials": tuple(pointer(item) for item in mesh.materials),
    }


def shape_key_state(keys):
    return {
        "reference": pointer(keys.reference_key),
        "use_relative": keys.use_relative,
        "eval_time": keys.eval_time,
        "blocks": tuple((pointer(key), key.name, pointer(key.relative_key), key.value,
                         key.slider_min, key.slider_max, key.mute, key.interpolation,
                         key.vertex_group, len(key.data), array_hash(key.data, "co", 3, "f"))
                        for key in keys.key_blocks),
    }


def fcurve_state(curve):
    driver = curve.driver
    return {
        "rna": rna_values(curve, 0),
        "group": (pointer(curve.group), curve.group.name if curve.group else None),
        "keys": tuple(rna_values(key, 0) for key in curve.keyframe_points),
        "samples": tuple(tuple(point.co) for point in curve.sampled_points),
        "modifiers": tuple(rna_values(modifier) for modifier in curve.modifiers),
        "driver": rna_values(driver, 0) if driver else None,
        "variables": tuple((rna_values(variable, 0),
                            tuple(rna_values(target, 0) for target in variable.targets))
                           for variable in driver.variables) if driver else (),
    }


def action_state(action):
    legacy = getattr(action, "fcurves", ())
    layers = []
    for layer in getattr(action, "layers", ()):
        strips = []
        for strip in layer.strips:
            bags = []
            for bag in getattr(strip, "channelbags", ()):
                bags.append((bag.slot_handle,
                             tuple(fcurve_state(curve) for curve in bag.fcurves),
                             tuple(rna_values(group, 0) for group in getattr(bag, "groups", ()))))
            strips.append((rna_values(strip, 0), tuple(bags)))
        layers.append((rna_values(layer, 0), tuple(strips)))
    return {
        "name": action.name,
        "rna": rna_values(action, 0),
        "legacy_curves": tuple(fcurve_state(curve) for curve in legacy),
        "slots": tuple((pointer(slot), slot.handle, slot.identifier,
                        getattr(slot, "target_id_type", None), rna_values(slot, 0))
                       for slot in getattr(action, "slots", ())),
        "layers": tuple(layers),
    }


def animation_state(owner):
    animation = getattr(owner, "animation_data", None)
    if animation is None:
        return None
    tracks = []
    for track in animation.nla_tracks:
        strips = []
        for strip in track.strips:
            strips.append((rna_values(strip, 0), pointer(strip.action),
                           pointer(getattr(strip, "action_slot", None)),
                           tuple(fcurve_state(curve) for curve in strip.fcurves),
                           tuple(rna_values(modifier) for modifier in strip.modifiers)))
        tracks.append((rna_values(track, 0), tuple(strips)))
    return (rna_values(animation, 0), pointer(animation.action),
            pointer(getattr(animation, "action_slot", None)),
            tuple(fcurve_state(curve) for curve in animation.drivers), tuple(tracks))


def armature_state(obj):
    return {
        "rest": tuple((pointer(bone), bone.name, pointer(bone.parent), matrix(bone.matrix_local),
                       tuple(bone.head_local), tuple(bone.tail_local), rna_values(bone, 0))
                      for bone in obj.data.bones),
        "pose": tuple((pointer(bone), bone.name, matrix(bone.matrix_basis), matrix(bone.matrix),
                       pointer(bone.custom_shape), pointer(bone.custom_shape_transform),
                       rna_values(bone, 0),
                       tuple(rna_values(constraint) for constraint in bone.constraints))
                      for bone in obj.pose.bones),
        "collections": tuple((pointer(collection), collection.name, pointer(collection.parent),
                              tuple(pointer(bone) for bone in collection.bones),
                              rna_values(collection, 0))
                             for collection in obj.data.collections_all),
    }


def object_state(obj):
    return {
        "type": obj.type,
        "data": pointer(obj.data),
        "parent": (pointer(obj.parent), obj.parent_type, obj.parent_bone),
        "matrices": (matrix(obj.matrix_world), matrix(obj.matrix_basis), matrix(obj.matrix_parent_inverse)),
        "channels": (tuple(obj.location), tuple(obj.rotation_euler), tuple(obj.rotation_quaternion),
                     tuple(obj.rotation_axis_angle), obj.rotation_mode, tuple(obj.scale)),
        "groups": tuple((pointer(group), group.name, group.index, group.lock_weight)
                        for group in obj.vertex_groups),
        "shape_index": obj.active_shape_key_index,
        "collections": tuple(sorted(pointer(collection) for collection in obj.users_collection)),
        "constraints": tuple(rna_values(constraint) for constraint in obj.constraints),
        "modifiers": tuple(rna_values(modifier) for modifier in obj.modifiers),
        "armature": armature_state(obj) if obj.type == "ARMATURE" else None,
    }


def all_ids():
    for prop in bpy.data.bl_rna.properties:
        if prop.type != "COLLECTION" or prop.identifier == "all_ids":
            continue
        for item in getattr(bpy.data, prop.identifier):
            if isinstance(item, bpy.types.ID):
                yield prop.identifier, item


def snapshot():
    """Keep compact hashes; object/data/collection names are the permitted change."""
    state = {}
    allowed_name_domains = {"objects", "meshes", "curves", "armatures", "collections"}
    for domain, item in all_ids():
        key = (domain, pointer(item))
        state[("identity",) + key] = None if domain in allowed_name_domains else item.name
        animation = animation_state(item)
        if animation is not None:
            state[("animation",) + key] = digest(animation)
    for obj in bpy.data.objects:
        state[("object", pointer(obj))] = digest(object_state(obj))
    for mesh in bpy.data.meshes:
        state[("mesh", pointer(mesh))] = digest(mesh_state(mesh))
    for keys in bpy.data.shape_keys:
        state[("shape_keys", pointer(keys))] = digest(shape_key_state(keys))
    for action in bpy.data.actions:
        state[("action", pointer(action))] = digest(action_state(action))
    for collection in bpy.data.collections:
        state[("collection", pointer(collection))] = (
            tuple(sorted(pointer(obj) for obj in collection.objects)),
            tuple(sorted(pointer(child) for child in collection.children)),
            collection.hide_viewport, collection.hide_render, collection.hide_select,
        )
    return state


def assert_snapshot(before, after):
    assert before.keys() == after.keys(), "Cleanup added or removed scene data."
    differences = [str(key) for key in before if before[key] != after[key]]
    assert not differences, "Cleanup changed authored scene state: " + ", ".join(differences[:20])


def validate_resources(manager, skirt):
    rigs, resources, skirts = 0, 0, 0
    for armature in bpy.data.objects:
        if armature.type != "ARMATURE":
            continue
        if not any(key.startswith("character_designer") for key in armature.data.keys()):
            continue
        entries = manager._resources(armature)
        rigs += 1
        resources += len(entries)
        for entry in entries:
            assert entry["collection"] is not None, "A widget collection reference is missing."
            assert set(entry["collection"].objects.keys()) == entry["objects"], "Widget membership changed."
    for source in bpy.data.objects:
        if skirt.RECORD_KEY in source:
            record = skirt.read_record(source)
            skirt._check_existing_geometry(source, record)
            skirts += 1
    return {"armatures": rigs, "widget_resources": resources, "skirts": skirts}


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    expect_clean = bool(args and args[-1] == "--expect-clean")
    if expect_clean:
        args = args[:-1]
    if not 1 <= len(args) <= 2:
        raise SystemExit("Expected -- input.blend [report.json] [--expect-clean]")
    source = Path(args[0]).resolve(strict=True)
    report_path = Path(args[1]).resolve() if len(args) == 2 else None
    assert source.suffix.lower() == ".blend", "Input must be a saved .blend file."
    assert report_path is None or report_path != source, "Report cannot overwrite the input."
    original_hash = file_hash(source)
    report = {"input": str(source), "sha256_before": original_hash, "status": "failed",
              "expect_clean": expect_clean}
    try:
        # Isolate the snapshot from any previously registered cleanup callback.
        for handler in tuple(bpy.app.handlers.load_post):
            if getattr(handler, "__module__", "") == "character_designer.generated_names":
                bpy.app.handlers.load_post.remove(handler)
        bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
        from character_designer import generated_names, widget_collections, skirt_rig
        assert Path(generated_names.__file__).resolve().is_relative_to(ROOT / "addons"), "Canonical source was not imported."
        bpy.context.view_layer.update()
        before = snapshot()
        result = generated_names.clean_generated_names(context=bpy.context)
        assert not result["skipped"], "Verified scene cleanup skipped resources: " + repr(result["skipped"])
        if expect_clean:
            assert not result["renamed"], "Expected an already-cleaned scene, but names still needed migration: " + repr(result["renamed"])
        else:
            assert result["renamed"], "Expected legacy generated names in this saved validation scene."
        assert_snapshot(before, snapshot())
        report["resources"] = validate_resources(widget_collections, skirt_rig)
        second = generated_names.clean_generated_names(context=bpy.context)
        assert second == {"renamed": [], "skipped": []}, "Second cleanup was not idempotent: " + repr(second)
        assert_snapshot(before, snapshot())
        report.update(status="passed", renamed_count=len(result["renamed"]), renamed=result["renamed"])
    except Exception:
        report["error"] = traceback.format_exc()
        raise
    finally:
        report["sha256_after"] = file_hash(source)
        report["input_unchanged"] = report["sha256_after"] == original_hash
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        assert report["input_unchanged"], "Input .blend changed on disk."
        print("REAL_GENERATED_NAMES_REPORT", json.dumps({key: value for key, value in report.items()
              if key not in {"renamed", "error"}}, ensure_ascii=False))
        for rename in report.get("renamed", ())[:12]:
            print("RENAMED", rename["from"], "->", rename["to"])
    print("REAL_GENERATED_NAMES_OK")


if __name__ == "__main__":
    main()
