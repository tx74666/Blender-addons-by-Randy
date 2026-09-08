"""Open an actual 0.40.2 Grouped result under the current Per Strand-only add-on.

The old generator runs only from the frozen release ZIP in a disposable Blender
subprocess. Current runtime code never gets a hidden legacy generation route.
Run with --background --factory-startup --disable-autoexec --python-exit-code 1.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OLD_CREATE = bool(ARGS and ARGS[0] == "legacy-create")
sys.path.insert(0, str(Path(ARGS[1]) if OLD_CREATE else ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
# Import the chosen runtime before shared fixture helpers add the current path.
import character_designer as cd
from character_designer import hair_bones as ui
from character_designer import hair_bones_groups as groups
from character_designer import hair_bones_rig as rig
from character_designer import hair_bones_topology as topology
from character_designer import hair_bones_variants as variants
from test_hair_bones_topology_blender import Fixture, select
from test_hair_bones_rig_blender import activate, make_armature, reset, weights, evaluated_points, assert_points


def action_data(owner):
    action = owner.animation_data.action if owner.animation_data else None
    if not action:
        return None
    curves = [curve for layer in action.layers for strip in layer.strips
              for bag in strip.channelbags for curve in bag.fcurves]
    return {"name": action.name, "curves": [
        (curve.data_path, curve.array_index,
         [(tuple(point.co), tuple(point.handle_left), tuple(point.handle_right), point.interpolation)
          for point in curve.keyframe_points]) for curve in curves]}


def saved_artwork(mesh, armature):
    """Serialize artist-owned shape, skinning and animation across processes."""
    keys = mesh.data.shape_keys
    record = {
        "vertices": [tuple(vertex.co) for vertex in mesh.data.vertices],
        "groups": [(group.name, group.lock_weight) for group in mesh.vertex_groups],
        "weights": weights(mesh),
        "keys": [(key.name, key.value, [tuple(point.co) for point in key.data]) for key in keys.key_blocks],
        "key_action": action_data(keys), "rig_action": action_data(armature),
        "bones": [(bone.name, tuple(bone.head_local), tuple(bone.tail_local),
                   bone.parent.name if bone.parent else None) for bone in armature.data.bones],
        "pose": [(pose.name, tuple(tuple(row) for row in pose.matrix_basis)) for pose in armature.pose.bones],
        "record": mesh[rig.RECORD_KEY],
    }
    return json.loads(json.dumps(record))


def create_old(directory):
    assert tuple(cd.bl_info["version"]) == (0, 40, 2)
    assert str(Path(ARGS[1]).resolve()).lower() in str(Path(cd.__file__).resolve()).lower()
    cd.register()
    reset()
    make_armature()
    builder = Fixture()
    layers = tuple(builder.tube(sides=5, rows=5 + index % 2, origin=(index * 0.8, 0, 0))
                   for index in range(4))
    source = builder.object("Legacy Captured Hair")
    obj, plans = topology.select_strands(bpy.context)
    groups.capture_plans(obj, plans)
    select(source, [layers[index][-1][0] for index in (0, 1)])
    first_group = groups.group_selected(bpy.context)
    select(source, [layers[index][-1][0] for index in (2, 3)])
    groups.group_selected(bpy.context)
    guide = groups.create_group_guide(bpy.context, first_group, point_count=4)
    guide.data.splines[0].points[1].co.x += 0.2
    _, shared = groups.build_plans(bpy.context, mode="GROUPED", source=source)
    result = variants.build_variant(bpy.context, source, shared, mode="GROUPED", bone_count=3)
    assert len(result["chains"]) == 2
    mesh, armature = result["mesh"], result["armature"]
    records = rig._read_records(mesh)["chains"]
    assert all(len(chain["members"]) == 2 for chain in records)
    chain = records[0]
    pose = armature.pose.bones[chain["bones"][0]]
    pose.rotation_mode = "XYZ"
    pose.rotation_euler.x = 0.27
    pose.keyframe_insert("rotation_euler", frame=1)
    pose.rotation_euler.x = -0.14
    pose.keyframe_insert("rotation_euler", frame=5)
    tip = chain["members"][0]["layers"][-1][0]
    mesh.vertex_groups[chain["bones"][-1]].add([tip], 0.79, "REPLACE")
    mesh.vertex_groups[chain["bones"][-2]].add([tip], 0.21, "REPLACE")
    artist = mesh.data.shape_keys.key_blocks["ArtistKey"]
    artist.value = 0.3
    artist.keyframe_insert("value", frame=1)
    artist.value = 0.7
    artist.keyframe_insert("value", frame=5)
    bpy.context.scene.frame_set(1)
    settings = ui._settings(bpy.context)
    settings.mode = "GROUPED"
    settings.active_group = first_group
    settings.source = source
    settings.active_variant = result["collection"].name
    expected = {"source": source.name, "mesh": mesh.name, "rig": armature.name,
                "version": result["collection"].name, "guide": guide.name,
                "capture": source[groups.GROUPS_KEY], "artwork": saved_artwork(mesh, armature),
                "points": [list(point) for point in evaluated_points(mesh)]}
    (directory / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    bpy.ops.wm.save_as_mainfile(filepath=str(directory / "legacy.blend"), check_existing=False)
    print("LEGACY_0402_GROUPED_FIXTURE_OK")


def check_current(directory):
    from character_designer import hair_bones_binding as binding

    expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    cd.register()
    bpy.ops.wm.open_mainfile(filepath=str(directory / "legacy.blend"))
    bpy.context.scene.frame_set(1)
    source, mesh, armature = (bpy.data.objects[expected[name]] for name in ("source", "mesh", "rig"))
    old = bpy.data.collections[expected["version"]]
    assert old[variants.MODE_KEY] == "GROUPED"
    assert len(groups.read_groups(source)) == 2
    assert all(len(record["members"]) == 2 for record in groups.read_groups(source))
    assert variants.source_for(mesh) is source and variants.source_for(armature) is source
    assert saved_artwork(mesh, armature) == expected["artwork"]
    assert_points(evaluated_points(mesh), tuple(Vector(point) for point in expected["points"]), "Old saved pose still evaluates")
    settings = ui._settings(bpy.context)
    # Retired enum values can survive an add-on refresh / old file. Ignore them.
    settings["mode"] = 1
    settings["active_group"] = 424242
    settings["active_variant"] = expected["version"]
    settings.source = source
    assert "mode" not in settings.bl_rna.properties
    assert "active_variant" not in settings.bl_rna.properties
    for name in ("hair_show_version", "hair_build_version"):
        try:
            getattr(bpy.ops.character_designer, name).get_rna_type()
        except (KeyError, RuntimeError):
            pass
        else:
            raise AssertionError("Retired version operator is still registered: " + name)
    # Native legacy results retain their reader API after the version UI retires.
    variants.show_variant(bpy.context, old)
    assert bpy.context.object is armature and bpy.context.mode == "POSE"
    assert bpy.ops.character_designer.hair_show_source() == {"FINISHED"}
    _, independent = groups.build_plans(bpy.context, source=source)
    assert len(independent) == 4 and all(not plan.get("members") for plan in independent)
    settings.bone_count = 4
    result = binding.bind_hair(bpy.context, source, independent, bone_count=4,
                               armature=old[variants.ATTACHMENT_KEY])
    assert result["armature"] is old[variants.ATTACHMENT_KEY]
    assert binding.is_bound(source)
    assert variants.variants_for(source) == (old,), "The current workflow must not create a new mesh copy"
    record = rig._read_records(source)
    assert len(record["chains"]) == 4
    assert all(len(chain["bones"]) == 4 and not chain.get("members") for chain in record["chains"])
    assert source[groups.GROUPS_KEY] == expected["capture"]
    assert bpy.data.objects.get(expected["guide"]) is not None
    assert saved_artwork(mesh, armature) == expected["artwork"]
    variants.show_variant(bpy.context, old)
    baseline = evaluated_points(mesh)
    chain = rig._read_records(mesh)["chains"][0]
    pose = armature.pose.bones[chain["bones"][1]]
    original_matrix = pose.matrix_basis.copy()
    pose.rotation_mode = "XYZ"
    pose.rotation_euler.y += 0.24
    changed = evaluated_points(mesh)
    assert max((a - b).length for a, b in zip(changed, baseline)) > 0.03, "Legacy controls remain editable"
    pose.matrix_basis = original_matrix
    bpy.context.scene.frame_set(1)
    # The extra edited bone had no animation; restoring its basis is sufficient.
    assert_points(evaluated_points(mesh), baseline, "Legacy manual edit is reversible")
    assert saved_artwork(mesh, armature) == expected["artwork"]
    assert bpy.ops.character_designer.hair_show_source() == {"FINISHED"}
    binding.remove_hair_binding(bpy.context, source)
    assert not binding.is_bound(source)
    assert bpy.ops.character_designer.hair_clear_groups() == {"FINISHED"}
    assert bpy.data.objects.get(expected["guide"]) is not None
    assert saved_artwork(mesh, armature) == expected["artwork"]
    assert variants.variants_for(source) == (old,)
    cd._validate_registration_integrity()
    cd.unregister()
    print("HAIR_LEGACY_GROUPED_COMPAT_TESTS_OK")


def main():
    with tempfile.TemporaryDirectory(prefix="hair-legacy-compat-") as temporary:
        directory = Path(temporary)
        old_addons = directory / "old-addons"
        with zipfile.ZipFile(ROOT / "dist" / "character_designer-0.40.2.zip") as archive:
            assert all(Path(name).parts[0] == "character_designer" and ".." not in Path(name).parts
                       for name in archive.namelist())
            archive.extractall(old_addons)
        process = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
                                  "--python-exit-code", "1", "--python", str(Path(__file__).resolve()),
                                  "--", "legacy-create", str(old_addons), str(directory)],
                                 capture_output=True, text=True, timeout=120)
        assert process.returncode == 0 and "LEGACY_0402_GROUPED_FIXTURE_OK" in process.stdout, (process.stdout, process.stderr)
        check_current(directory)


if __name__ == "__main__":
    if OLD_CREATE:
        create_old(Path(ARGS[2]))
    else:
        main()
