"""Hair/Dress display grouping, legacy migration and bake visibility checks."""

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import hair_bones_rig, skirt_physics, skirt_rig
from test_hair_bones_rig_blender import build, make_armature, make_hair, reset
from test_skirt_topology_blender import frustum


def collection_state(rig):
    return tuple((item.name, tuple(item.bones.keys()), item.is_visible) for item in rig.data.collections_all)


def test_hair_reuses_owned_collection():
    reset()
    rig = make_armature()
    hair, plans = make_hair()
    result = build(hair, plans[:1], armature=rig, parent_bone="spine.006")
    collection = next(c for c in rig.data.collections_all
                      if c.get(hair_bones_rig.OWNER_KEY) == hair_bones_rig.OWNER_VALUE)
    assert collection.name == "Hair"
    collection.name = "Hair Controls"
    artist = rig.data.collections.new("Artist Head")
    artist.assign(rig.data.bones["spine.006"])
    build(hair, plans[:1], armature=rig, parent_bone="spine.006")
    assert collection.name == "Hair", "Reusing existing binding must also rename its owned collection"
    collection.name = "Hair Controls"
    build(hair, plans[1:], armature=rig, parent_bone="spine.006")
    owned = [c for c in rig.data.collections_all
             if c.get(hair_bones_rig.OWNER_KEY) == hair_bones_rig.OWNER_VALUE]
    assert len(owned) == 1 and owned[0] == collection and collection.name == "Hair"
    assert len(collection.bones) == 8
    assert tuple(artist.bones.keys()) == ("spine.006",)


def make_skirt():
    reset()
    source = frustum()
    record = skirt_rig.build_skirt(bpy.context, source)
    rig = source[skirt_rig.RIG_KEY]
    if bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return source, rig, record


def legacy_layout(rig, record):
    for item in list(rig.data.collections_all):
        rig.data.collections.remove(item)
    groups = skirt_rig._bone_collection_layout(record)
    for title, names, visible in zip(("Skirt Controls", "Skirt Deform", "Skirt Mechanism"),
                                     groups, (True, False, False)):
        collection = rig.data.collections.new(title)
        collection.is_visible = visible
        for name in names:
            collection.assign(rig.data.bones[name])
            rig.data.bones[name].hide = False


def assert_compact_skirt(rig, record):
    collection = next(c for c in rig.data.collections_all if c.get(skirt_rig.OWNER_KEY) == record["owner"])
    assert set(collection.bones.keys()) == set(rig.data.bones.keys())
    controls, deform, mechanism = skirt_rig._bone_collection_layout(record)
    assert all(not rig.data.bones[name].hide for name in controls)
    assert all(rig.data.bones[name].hide for name in deform | mechanism)
    assert len(collection.bones) == 115


def test_skirt_defaults_and_baked_visibility():
    source, rig, record = make_skirt()
    assert tuple(rig.data.collections.keys()) == ("Dress",)
    assert_compact_skirt(rig, record)
    assert not skirt_rig.migrate_skirt_bone_collections(rig)
    _, deform, _ = skirt_rig._bone_collection_layout(record)
    assert {bone.name for bone in rig.data.bones if bone.use_deform} == deform | {record["controls"]["waist"]}
    export = skirt_physics._export_objects(bpy.context, source, rig, record)
    try:
        output = export[1]
        assert len(output.data.bones) == 33
        assert all(not bone.hide and bone.use_deform for bone in output.data.bones)
        assert all(c.is_visible for c in output.data.collections_all)
    finally:
        skirt_physics._remove_export(export[0])
    assert_compact_skirt(rig, record)


def test_skirt_migration_preserves_motion_and_user_collection():
    source, rig, record = make_skirt()
    legacy_layout(rig, record)
    artist = rig.data.collections.new("Skirt")
    artist.assign(rig.data.bones[record["controls"]["hem"]])
    artist_dress = rig.data.collections.new("Dress")
    artist_dress.assign(rig.data.bones[record["controls"]["mid"]])
    artist_dress.is_visible = False
    rig.pose.bones[record["controls"]["hem"]].location.x = 0.12
    bpy.context.view_layer.update()
    matrices = {bone.name: bone.matrix.copy() for bone in rig.pose.bones}
    original_record = source[skirt_rig.RECORD_KEY]
    assert skirt_rig.migrate_skirt_bone_collections(rig)
    assert_compact_skirt(rig, record)
    assert tuple(artist.bones.keys()) == (record["controls"]["hem"],)
    assert artist.name == "Skirt"
    assert artist_dress.name == "Dress" and not artist_dress.is_visible
    assert tuple(artist_dress.bones.keys()) == (record["controls"]["mid"],)
    owned = next(c for c in rig.data.collections_all if c.get(skirt_rig.OWNER_KEY) == record["owner"])
    assert owned.name == "Dress.001"
    assert not any(rig.data.collections.get(title) for title in
                   ("Skirt Controls", "Skirt Deform", "Skirt Mechanism"))
    assert source[skirt_rig.RECORD_KEY] == original_record
    bpy.context.view_layer.update()
    assert all(max(abs(bone.matrix[row][col] - matrices[bone.name][row][col])
                   for row in range(4) for col in range(4)) < 1e-6 for bone in rig.pose.bones)
    assert not skirt_rig.migrate_skirt_bone_collections(rig)


def test_owned_skirt_rename_preserves_display_and_physics():
    source, rig, record = make_skirt()
    collection = next(c for c in rig.data.collections_all if c.get(skirt_rig.OWNER_KEY) == record["owner"])
    collection.name = "Skirt"
    collection.is_visible = False
    artist = rig.data.collections.new("Artist Dress")
    artist.assign(rig.data.bones[record["controls"]["hem"]])
    rig.pose.bones[record["controls"]["hem"]].location.x = .12
    bpy.context.view_layer.update()
    rest = {b.name: (tuple(b.head_local), tuple(b.tail_local), b.use_deform,
                    b.parent.name if b.parent else None) for b in rig.data.bones}
    poses = {b.name: b.matrix.copy() for b in rig.pose.bones}
    hidden = {b.name: b.hide for b in rig.data.bones}
    membership = tuple(collection.bones.keys())
    original_record = source[skirt_rig.RECORD_KEY]
    assert skirt_rig.migrate_skirt_bone_collections(rig)
    assert collection.name == "Dress" and not collection.is_visible
    assert tuple(collection.bones.keys()) == membership
    assert {b.name: b.hide for b in rig.data.bones} == hidden
    assert tuple(artist.bones.keys()) == (record["controls"]["hem"],)
    assert source[skirt_rig.RECORD_KEY] == original_record
    assert {b.name: (tuple(b.head_local), tuple(b.tail_local), b.use_deform,
                    b.parent.name if b.parent else None) for b in rig.data.bones} == rest
    bpy.context.view_layer.update()
    assert all(max(abs(pb.matrix[r][c] - poses[pb.name][r][c]) for r in range(4) for c in range(4)) < 1e-6
               for pb in rig.pose.bones)
    assert not skirt_rig.migrate_skirt_bone_collections(rig)
    collection.name = "Skirt"
    skirt_rig.build_skirt(bpy.context, source)
    assert collection.name == "Dress", "Reusing an existing skirt must migrate its owned display group"
    physics_record = skirt_physics.add_physics(bpy.context, source)
    assert physics_record["physics"]
    assert skirt_rig.read_record(source)["physics"] == physics_record["physics"]
    assert not skirt_rig.migrate_skirt_bone_collections(rig)
    assert_compact_skirt(rig, physics_record)
    group = source.vertex_groups.new(name="Artist Weights")
    group.add([0], .4, 'REPLACE')
    rig_name = rig.name
    skirt_rig.remove_skirt(bpy.context, source)
    assert bpy.data.objects.get(rig_name) is None
    assert skirt_rig.read_record(source) is None
    retained = source.vertex_groups.get("Artist Weights")
    assert retained is not None and abs(retained.weight(0) - .4) < 1e-6


def test_owned_skirt_rename_preserves_artist_name_collision():
    source, rig, record = make_skirt()
    owned = next(c for c in rig.data.collections_all if c.get(skirt_rig.OWNER_KEY) == record["owner"])
    owned.name = "Skirt"
    artist = rig.data.collections.new("Dress")
    artist.assign(rig.data.bones[record["controls"]["hem"]])
    assert skirt_rig.migrate_skirt_bone_collections(rig)
    assert owned.name == "Dress.001"
    assert artist.name == "Dress" and not artist.get(skirt_rig.OWNER_KEY)
    assert tuple(artist.bones.keys()) == (record["controls"]["hem"],)
    assert not skirt_rig.migrate_skirt_bone_collections(rig)


def test_skirt_migration_refuses_edited_or_unowned_data():
    source, rig, record = make_skirt()
    legacy_layout(rig, record)
    rig.data.collections["Skirt Deform"].assign(rig.data.bones[record["controls"]["hem"]])
    before = collection_state(rig), tuple(bone.hide for bone in rig.data.bones)
    try:
        skirt_rig.migrate_skirt_bone_collections(rig)
        raise AssertionError("Edited legacy collection must be refused")
    except skirt_rig.SkirtRigError:
        pass
    assert (collection_state(rig), tuple(bone.hide for bone in rig.data.bones)) == before
    del rig[skirt_rig.OWNER_KEY]
    assert not skirt_rig.migrate_skirt_bone_collections(rig)
    assert (collection_state(rig), tuple(bone.hide for bone in rig.data.bones)) == before


for test in (test_hair_reuses_owned_collection, test_skirt_defaults_and_baked_visibility,
             test_skirt_migration_preserves_motion_and_user_collection,
             test_owned_skirt_rename_preserves_display_and_physics,
             test_owned_skirt_rename_preserves_artist_name_collision,
             test_skirt_migration_refuses_edited_or_unowned_data):
    test()
    print("PASS", test.__name__)
print("ACCESSORY_BONE_COLLECTIONS_OK")
